import base64
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime, time
import secrets
from dataclasses import dataclass

# Import flask
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate


# Import my credentials
import secretstuff

# Create the app
app = Flask(__name__)

# Create the DB
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///songs.sqlite3'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config["JSON_SORT_KEYS"] = False
db = SQLAlchemy(app)
migrate = Migrate(app, db)


# Define the model
@dataclass
class User(db.Model):
    id:str = db.Column(db.String(80), primary_key=True)
    spotify_username:str = db.Column(db.String(80), unique=True)
    spotify_token:str = db.Column(db.String(120), unique=True)
    spotify_token_last_refreshed:datetime = db.Column(db.DateTime)
    last_called:datetime = db.Column(db.DateTime)
    api_calls:int = db.Column(db.Integer)


# Define global paths and uris
app_uri = "http://localhost:5001/"
redirect_path = "callback/"
redirect_uri = app_uri + redirect_path

spotify_recently_listened_api_uri = "https://api.spotify.com/v1/me/player/recently-played"
spotify_user_api_uri = "https://api.spotify.com/v1/me"


def get_user_id(token = None):
    if token is None:
        return None

    headers = {"Authorization": "Bearer " + token}

    response = requests.get(spotify_user_api_uri, headers=headers)
    return response.json()['id']


def get_recently_listened(token = None):
    midnight = datetime.combine(datetime.today(), time.min)
    midnight = int(midnight.timestamp())

    if token is None:
        return None
    headers = {
        "Authorization": "Bearer " + token
    }
    params = {
        "limit": 50,
        "after": midnight
    }
    response = requests.get(spotify_recently_listened_api_uri, headers=headers, params=params)

    json_response = response.json()

    track_names = []

    for item in json_response['items']:

        track_name = item['track']['name']
        artist_name = item['track']['artists'][0]['name']

        track_names.append({
            'artist': artist_name,
            'track_name': track_name
        })

    return track_names


@app.route('/')
def index():
    auth_uri =  "https://accounts.spotify.com/authorize" + \
                "?client_id=" + secretstuff.spotify_client_id + \
                "&response_type=code" + \
                "&redirect_uri=" + redirect_uri + \
                "&scope=user-read-recently-played"
    return render_template(
        'index.html',
        auth_uri=auth_uri
    )


@app.route('/callback/')
def auth_callback():
    # Get the code from the callback
    spotify_code = request.args.get('code')

    if not spotify_code:
        return "No code found"

    # Request a token
    token_uri = "https://accounts.spotify.com/api/token"
    params = {
        "grant_type": "authorization_code",
        "code": spotify_code,
        "redirect_uri": redirect_uri,
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }
    auth = HTTPBasicAuth(secretstuff.spotify_client_id, secretstuff.spotify_client_secret)
    response = requests.post(token_uri, params=params, headers=headers, auth=auth)

    if not response.ok:
        return "Error getting token"

    access_token = response.json()['access_token']

    # Get the user's spotify username
    spotify_username = get_user_id(access_token)

    # Check whether the spotify username is already in the database
    user = User.query.filter_by(
        spotify_username = spotify_username
    ).first()

    # Create a new user if that spotify username is not in the database already ...
    if not user:
        new_id = secrets.token_hex(40)

        user = User(
            id = new_id,
            spotify_username = spotify_username,
            spotify_token = access_token,
            api_calls = 0,
            spotify_token_last_refreshed = datetime.now()
        )
        db.session.add(user)
    # ... or update the user's token if the user is in the database
    else:
        user.spotify_token = access_token
        user.spotify_token_last_refreshed = datetime.now()

    db.session.commit()

    return jsonify(user)


# Run the app
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5001, debug=True)
