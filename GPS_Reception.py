import threading
import pynmea2
import telnetlib
import time
import json
import csv
import glob
import os
from flask import Flask, render_template, jsonify, request

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
            trace_data.append((latitude, longitude))

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
def connect_to_gps():
    HOST = '192.168.65.34'  # IP address of your GPS device
    PORT = 8080  # Port number for the TCP server on your GPS device

    try:
        # Connect to the GPS device
        tn = telnetlib.Telnet(HOST, PORT)
        print("Connected to GPS device")

        # Receive data from the GPS device
        while True:
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

# Start the GPS connection in a separate thread
gps_thread = threading.Thread(target=connect_to_gps)
gps_thread.start()

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
        trace_file_name_json = f"trace_{int(record_start_time)}.json"
        trace_file_name_csv = f"trace_{int(record_start_time)}.csv"
        trace_data_with_time = {
            "start_time": record_start_time,
            "end_time": record_end_time,
            "duration": recording_duration,
            "traces": trace_data
        }
        
        with open(trace_file_name_json, 'w') as trace_file:
            json.dump(trace_data_with_time, trace_file)
        
        with open(trace_file_name_csv, 'w', newline='') as csv_file:
            csv_writer = csv.writer(csv_file)
            csv_writer.writerow(['Latitude', 'Longitude'])
            for trace in trace_data:
                csv_writer.writerow(trace)
        
        print(f"Trace data saved to {trace_file_name_json} and {trace_file_name_csv}")

    return "Recording stopped", 200

@app.route('/get_traces')
def get_traces():
    with data_lock:
        recording_duration = record_end_time - record_start_time
        response = jsonify({
            "traces": trace_data,
            "recording_duration": recording_duration
        })
        return response

@app.route('/list_traces')
def list_traces():
    trace_files = glob.glob("trace_*.json")  # Adjust the pattern if necessary
    trace_files.sort()  # Optional: Sort the list if needed
    return jsonify(trace_files)

@app.route('/get_trace/<trace_file>')
def get_trace(trace_file):
    with data_lock:
        trace_file_path = os.path.join(trace_file)
        if os.path.exists(trace_file_path):
            with open(trace_file_path, 'r') as file:
                trace_data = json.load(file)
            return jsonify(trace_data)
        else:
            return "Trace file not found", 404

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
                latitude, longitude = map(float, row)
                traces.append((latitude, longitude))
        
        return jsonify(traces=traces), 200

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
