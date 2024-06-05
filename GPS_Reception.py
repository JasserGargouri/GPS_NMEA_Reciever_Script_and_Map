import threading
import telnetlib
import pynmea2
import folium
from flask import Flask, render_template, jsonify

app = Flask(__name__)

# Global variables to store the latest GPS data
latest_data = {
    "latitude": None,
    "longitude": None,
    "elevation": None
}

# Update the latest GPS data
def update_latest_data(latitude, longitude, elevation=None):
    global latest_data
    latest_data["latitude"] = latitude
    latest_data["longitude"] = longitude
    latest_data["elevation"] = elevation

# Parse NMEA sentence
def parse_nmea_sentence(sentence):
    try:
        msg = pynmea2.parse(sentence.strip())
        if isinstance(msg, pynmea2.types.talker.GGA):
            latitude = msg.latitude
            longitude = msg.longitude
            elevation = msg.altitude
            update_latest_data(latitude, longitude, elevation)
            print(f"Latitude: {latitude}, Longitude: {longitude}, Elevation: {elevation}")
        elif isinstance(msg, pynmea2.types.talker.RMC):
            latitude = msg.latitude
            longitude = msg.longitude
            update_latest_data(latitude, longitude)
            print(f"Latitude: {latitude}, Longitude: {longitude}")
    except pynmea2.ParseError as e:
        print(f"Parse error: {e}")

# Telnet connection function
def connect_to_gps():
    HOST = '172.20.10.2'  # IP address of your GPS device
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
    return jsonify(latest_data)

if __name__ == '__main__':
    app.run(debug=True)
