import threading
import time
import telnetlib
import csv
import glob
import os
from datetime import datetime
import pytz
from flask import Flask, request, jsonify, render_template
import pynmea2
from math import radians, cos, sin, sqrt, atan2
import threading

app = Flask(__name__)
data_lock = threading.Lock()

# Global variables for GPS data
latest_data = {
    "gps1": {"latitude": None, "longitude": None, "speed": None, "elevation": None},
    "gps2": {"latitude": None, "longitude": None, "speed": None, "elevation": None}
}
is_recording = False
record_start_time = None
record_end_time = None
trace_data = []

gps_threads = [None, None]
gps_stop_events = [threading.Event(), threading.Event()]

# Haversine formula to calculate the distance between two points
def haversine(lat1, lon1, lat2, lon2):
    R = 6371000  # radius of Earth in meters
    phi1 = radians(lat1)
    phi2 = radians(lat2)
    delta_phi = radians(lat2 - lat1)
    delta_lambda = radians(lon2 - lon1)
    a = sin(delta_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(delta_lambda / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c

# Update the latest GPS data
def update_latest_data(device_num, latitude, longitude, speed=None, elevation=None):
    global latest_data
    global is_recording
    global trace_data
    with data_lock:
        latest_data[f"gps{device_num}"]["latitude"] = latitude
        latest_data[f"gps{device_num}"]["longitude"] = longitude
        if speed is not None:
            latest_data[f"gps{device_num}"]["speed"] = speed
        if elevation is not None:
            latest_data[f"gps{device_num}"]["elevation"] = elevation

        # Add to trace if recording
        if is_recording:
            trace_data.append((time.time(), device_num, latitude, longitude, speed, elevation))

# Parse NMEA sentence
def parse_nmea_sentence(device_num, sentence):
    try:
        msg = pynmea2.parse(sentence.strip())
        if isinstance(msg, pynmea2.types.talker.GGA):
            latitude = msg.latitude
            longitude = msg.longitude
            elevation = msg.altitude
            update_latest_data(device_num, latitude, longitude, elevation=elevation)
        if isinstance(msg, pynmea2.types.talker.RMC):
            latitude = msg.latitude
            longitude = msg.longitude
            speed = msg.spd_over_grnd
            update_latest_data(device_num, latitude, longitude, speed=speed)
    except pynmea2.ParseError as e:
        print(f"Parse error: {e}")

# Telnet connection function
def connect_to_gps(host, port, device_num):
    try:
        # Connect to the GPS device
        tn = telnetlib.Telnet(host, port)
        print(f"Connected to GPS device {device_num}")

        # Receive data from the GPS device
        while not gps_stop_events[device_num - 1].is_set():
            data = tn.read_until(b"\n")  # Read until newline character
            data_str = data.decode('utf-8').strip()
            parse_nmea_sentence(device_num, data_str)  # Parse the received NMEA sentence

    except ConnectionRefusedError:
        print(f"Connection refused for GPS device {device_num}. Make sure the GPS device is running and reachable.")
    except Exception as e:
        print(f"An error occurred with GPS device {device_num}: {e}")
    finally:
        # Close the connection
        tn.close()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/gps_data')
def gps_data():
    with data_lock:
        response = jsonify(latest_data)
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Expires'] = 0
        response.headers['Pragma'] = 'no-cache'
        return response

@app.route('/connect_gps', methods=['POST'])
def connect_gps():
    global gps_threads
    data = request.json
    host = data['address']
    port = int(data['port'])
    device_num = int(data['deviceNum'])

    if gps_threads[device_num - 1] and gps_threads[device_num - 1].is_alive():
        return f"GPS {device_num} already connected", 400

    gps_stop_events[device_num - 1].clear()
    gps_threads[device_num - 1] = threading.Thread(target=connect_to_gps, args=(host, port, device_num))
    gps_threads[device_num - 1].start()

    return f"Connecting to GPS {device_num}", 200

@app.route('/start_recording', methods=['POST'])
def start_recording():
    global is_recording
    global record_start_time
    global trace_data
    with data_lock:
        is_recording = True
        record_start_time = time.time()
        trace_data = []
    return "Recording started", 200

@app.route('/stop_recording', methods=['POST'])
def stop_recording():
    global is_recording
    global record_end_time
    global trace_data
    with data_lock:
        is_recording = False
        record_end_time = time.time()

        # Save trace data to a file
        recording_duration = record_end_time - record_start_time
        local_timezone = pytz.timezone('Europe/Paris')  # Set your desired timezone here
        readable_start_time = datetime.fromtimestamp(record_start_time, local_timezone).strftime('%Y-%m-%d_%H-%M-%S')
        trace_file_name_csv = f"uploads/trace_{readable_start_time}.csv"
        trace_data_with_time = {
            "start_time": record_start_time,
            "end_time": record_end_time,
            "duration": recording_duration,
            "traces": trace_data
        }

        with open(trace_file_name_csv, 'w', newline='') as csv_file:
            csv_writer = csv.writer(csv_file)
            csv_writer.writerow(['Timestamp', 'Device', 'Latitude', 'Longitude', 'Speed', 'Elevation'])
            for trace in trace_data:
                csv_writer.writerow(trace)

        print(f"Trace data saved to {trace_file_name_csv}")

    return "Recording stopped", 200

@app.route('/list_traces')
def list_traces():
    trace_files = glob.glob("uploads/trace_*.csv")  # List all CSV files in uploads folder
    trace_files.sort()  # Optional: Sort the list if needed
    return jsonify(trace_files)

@app.route('/get_trace/<trace_file>')
def get_trace(trace_file):
    trace_path = os.path.join('uploads', trace_file)
    traces1 = []
    traces2 = []
    total_distance = 0.0
    duration = 0.0
    start_time = None
    end_time = None

    try:
        with open(trace_path, 'r') as csvfile:
            reader = csv.reader(csvfile)
            header = next(reader)  # Read the header row

            # Determine which columns are present
            has_timestamp = 'Timestamp' in header
            has_device = 'Device' in header

            previous_point1 = None
            previous_point2 = None

            for row in reader:
                if has_timestamp:
                    timestamp = float(row[header.index('Timestamp')])
                    latitude = float(row[header.index('Latitude')])
                    longitude = float(row[header.index('Longitude')])
                    if not start_time:
                        start_time = timestamp
                    end_time = timestamp
                else:
                    latitude = float(row[header.index('Latitude')])
                    longitude = float(row[header.index('Longitude')])

                if has_device:
                    device = int(row[header.index('Device')])
                else:
                    device = 1  # Default to device 1 if device column is not present

                if device == 1:
                    traces1.append([latitude, longitude])
                    if previous_point1:
                        total_distance += haversine(previous_point1[0], previous_point1[1], latitude, longitude)
                    previous_point1 = (latitude, longitude)
                elif device == 2:
                    traces2.append([latitude, longitude])
                    if previous_point2:
                        total_distance += haversine(previous_point2[0], previous_point2[1], latitude, longitude)
                    previous_point2 = (latitude, longitude)

        if start_time and end_time:
            duration = end_time - start_time

        trace_data = {
            'traces1': traces1,
            'traces2': traces2,
            'duration': duration,
            'distance': total_distance
        }

        return jsonify(trace_data)

    except FileNotFoundError:
        return jsonify({"error": "Trace file not found"}), 404

@app.route('/upload_trace', methods=['POST'])
def upload_trace():
    file = request.files['file']
    if file:
        file_path = os.path.join('uploads', file.filename)
        file.save(file_path)
        return jsonify({"status": "success", "filename": file.filename}), 200
    return jsonify({"status": "error", "message": "No file uploaded"}), 400

@app.route('/stop_all_gps', methods=['POST'])
def stop_all_gps():
    global gps_stop_events
    global latest_data

    # Stop all GPS threads
    for event in gps_stop_events:
        event.set()

    # Clear latest_data to stop updates
    with data_lock:
        latest_data["gps1"] = {"latitude": None, "longitude": None, "speed": None, "elevation": None}
        latest_data["gps2"] = {"latitude": None, "longitude": None, "speed": None, "elevation": None}

    return "Stopping all GPS connections and updates", 200


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
