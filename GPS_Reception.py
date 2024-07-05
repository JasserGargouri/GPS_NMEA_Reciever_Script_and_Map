from datetime import datetime
import threading
import pynmea2
import telnetlib
import time
import csv
import glob
import os
import pytz
from flask import Flask, render_template, jsonify, request
from math import radians, cos, sin, sqrt, atan2

app = Flask(__name__)

# Global variables to store the latest GPS data and traces
latest_data = {
    "latitude": None,
    "longitude": None,
    "elevation": None,
    "speed": None,
}

# Global variables to manage traces and recording state
trace_data = []
is_recording = False
record_start_time = None
record_end_time = None
data_lock = threading.Lock()
gps_thread = None
gps_stop_event = threading.Event()

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000  # Radius of the Earth in meters
    phi1 = radians(lat1)
    phi2 = radians(lat2)
    delta_phi = radians(lat2 - lat1)
    delta_lambda = radians(lon2 - lon1)
    a = sin(delta_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(delta_lambda / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c

# Update the latest GPS data
def update_latest_data(latitude, longitude, speed=None, elevation=None):
    global latest_data
    global is_recording
    global trace_data
    with data_lock:
        latest_data["latitude"] = latitude
        latest_data["longitude"] = longitude
        if speed is not None:
            latest_data["speed"] = speed
        if elevation is not None:
            latest_data["elevation"] = elevation
        
        # Add to trace if recording
        if is_recording:
            trace_data.append((time.time(), latitude, longitude, speed, elevation))

# Parse NMEA sentence
def parse_nmea_sentence(sentence):
    try:
        msg = pynmea2.parse(sentence.strip())
        if isinstance(msg, pynmea2.types.talker.GGA):
            latitude = msg.latitude
            longitude = msg.longitude
            elevation = msg.altitude
            update_latest_data(latitude, longitude, elevation=elevation)
        if isinstance(msg, pynmea2.types.talker.RMC):
            latitude = msg.latitude
            longitude = msg.longitude
            speed = msg.spd_over_grnd
            update_latest_data(latitude, longitude, speed=speed)
    except pynmea2.ParseError as e:
        print(f"Parse error: {e}")

# Telnet connection function
def connect_to_gps(host, port):
    try:
        # Connect to the GPS device
        tn = telnetlib.Telnet(host, port)
        print("Connected to GPS device")

        # Receive data from the GPS device
        while not gps_stop_event.is_set():
            data = tn.read_until(b"\n")  # Read until newline character
            data_str = data.decode('utf-8').strip()
            parse_nmea_sentence(data_str)  # Parse the received NMEA sentence

    except ConnectionRefusedError:
        print("Connection refused. Make sure the GPS device is running and reachable.")
    except Exception as e:
        print(f"An error occurred: {e}")
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
    global gps_thread
    if gps_thread and gps_thread.is_alive():
        return "Already connected", 400
    
    data = request.json
    host = data['address']
    port = int(data['port'])

    gps_stop_event.clear()
    gps_thread = threading.Thread(target=connect_to_gps, args=(host, port))
    gps_thread.start()

    return "Connecting to GPS", 200

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
            csv_writer.writerow(['Timestamp', 'Latitude', 'Longitude', 'Speed', 'Elevation'])
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
    traces = []
    total_distance = 0.0
    duration = 0.0
    start_time = None
    end_time = None

    try:
        with open(trace_path, 'r') as csvfile:
            reader = csv.reader(csvfile)
            header = next(reader)  # Read the header row
            has_timestamp = 'Timestamp' in header
            previous_point = None
            for row in reader:
                if has_timestamp:
                    timestamp, latitude, longitude, speed, elevation = row
                    timestamp = float(timestamp)
                    latitude, longitude = float(latitude), float(longitude)
                    if not start_time:
                        start_time = timestamp
                    end_time = timestamp
                else:
                    latitude, longitude = float(row[0]), float(row[1])
                traces.append([latitude, longitude])
                if previous_point:
                    total_distance += haversine(previous_point[0], previous_point[1], latitude, longitude)
                previous_point = (latitude, longitude)

        if has_timestamp:
            duration = end_time - start_time if start_time and end_time else 0.0

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"traces": traces, "duration": duration, "distance": total_distance})

@app.route('/upload_trace', methods=['POST'])
def upload_trace():
    if 'file' not in request.files:
        return "No file part", 400
    file = request.files['file']
    if file.filename == '':
        return "No selected file", 400
    if file:
        # Ensure the uploads directory exists
        if not os.path.exists('uploads'):
            os.makedirs('uploads')
        
        file_path = os.path.join("uploads", file.filename)
        file.save(file_path)
        
        # Read CSV file
        traces = []
        with open(file_path, 'r') as csv_file:
            csv_reader = csv.reader(csv_file)
            next(csv_reader)  # Skip header
            for row in csv_reader:
                latitude, longitude = float(row[0]), float(row[1])
                traces.append((latitude, longitude))
        
        return jsonify(traces=traces), 200

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
