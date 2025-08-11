from flask import Flask
from threading import Thread

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running"

def run():
    """Start the Flask server"""
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    """Launch the Flask server in a background thread"""
    t = Thread(target=run)
    t.daemon = True
    t.start()
