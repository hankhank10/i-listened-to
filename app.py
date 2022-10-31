import base64
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime, time, timedelta
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
app.config['SECRET_KEY'] = "Your_secret_string"
db = SQLAlchemy(app)
migrate = Migrate(app, db)


# Define the model
@dataclass
class User(db.Model):
    spotify_token_is_current:bool

    id:str = db.Column(db.String(80), primary_key=True)
    spotify_username:str = db.Column(db.String(80), unique=True)
    spotify_token:str = db.Column(db.String(120), unique=True)
    spotify_token_expires_at:datetime = db.Column(db.DateTime)
    spotify_refresh_token:str = db.Column(db.String(120), unique=True)
    spotify_token_last_refreshed:datetime = db.Column(db.DateTime)
    last_called:datetime = db.Column(db.DateTime)
    api_calls:int = db.Column(db.Integer)

    @property
    def spotify_token_is_current(self):
        return self.spotify_token_expires_at > datetime.now()


# Define global paths and uris
app_uri = "https://logspot.top/"
redirect_path = "callback/"
redirect_uri = app_uri + redirect_path

spotify_api_recently_listened_uri = "https://api.spotify.com/v1/me/player/recently-played"
spotify_api_user_uri = "https://api.spotify.com/v1/me"
spotify_api_token_uri = "https://accounts.spotify.com/api/token"


def get_user_id(token = None):
    if token is None:
        return None

    headers = {"Authorization": "Bearer " + token}

    response = requests.get(spotify_api_user_uri, headers=headers)
    return response.json()['id']


def get_recently_listened(token = None):
    midnight = datetime.combine(datetime.today(), time.min)
    midnight = int(midnight.timestamp()) * 1000

    if token is None:
        return None
    headers = {
        "Authorization": "Bearer " + token
    }
    params = {
        "limit": 50,
        "after": midnight
    }
    response = requests.get(spotify_api_recently_listened_uri, headers=headers, params=params)

    json_response = response.json()

    track_names = []

    for item in json_response['items']:
        track_name = item['track']['name']
        artist_name = item['track']['artists'][0]['name']

        track_names.append({
            'artist': artist_name,
            'track_name': track_name
        })

    track_names.reverse()

    return {
        'children': track_names
    }


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/spotify_authenticate')
def spotify_authenticate_redirect():

    auth_uri = "https://accounts.spotify.com/authorize" + \
               "?client_id=" + secretstuff.spotify_client_id + \
               "&response_type=code" + \
               "&redirect_uri=" + redirect_uri + \
               "&scope=user-read-recently-played"

    return redirect(auth_uri)


def get_new_spotify_token(spotify_code=None, refresh_token=None):

    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }
    auth = HTTPBasicAuth(secretstuff.spotify_client_id, secretstuff.spotify_client_secret)

    if spotify_code:
        params = {
            "grant_type": "authorization_code",
            "code": spotify_code,
            "redirect_uri": redirect_uri,
        }
    elif refresh_token:
        params = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
    else:
        return None

    response = requests.post(spotify_api_token_uri, params=params, headers=headers, auth=auth)
    return response


# This is the workflow the first time the user authenticates
@app.route('/callback/')
def auth_callback():
    # Get the code from the callback
    spotify_code = request.args.get('code')

    if not spotify_code:
        flash ("Login error: no code found")
        return redirect(url_for('index'))

    # Get the token
    response = get_new_spotify_token(spotify_code)

    print (response.json())

    if not response.ok:
        flash ("Login error: error getting token from spotify")
        return redirect(url_for('index'))

    # Get the user's access token and use it to get their spotify username
    access_token = response.json()['access_token']
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
            spotify_token_expires_at = datetime.now() + timedelta(seconds=response.json()['expires_in']),
            spotify_refresh_token = response.json()['refresh_token'],
            api_calls = 0,
            spotify_token_last_refreshed = datetime.now()
        )
        db.session.add(user)
    # ... or update the user's token if the user is in the database
    else:
        user.spotify_token = access_token
        #user.spotify_refresh_token = response.json()['refresh_token']
        user.spotify_token_expires_at = datetime.now() + timedelta(seconds=response.json()['expires_in'])
        user.spotify_token_last_refreshed = datetime.now()

    db.session.commit()

    return render_template('success.html', id=user.id)


@app.route('/getsongs/', methods=['POST'])
def get_songs():

    # Check that the JSON we have been sent is valid and matches a user
    try:
        posted_json = request.get_json()
        user_id = posted_json['user_id']
    except:
        return jsonify({
            'status': 'error',
            'message': 'No JSON found'
        }), 400

    if not user_id:
        return {
            'status': 'error',
            'message': 'No user_id provided'
        }, 400

    user = User.query.filter_by(
        id = user_id
    ).first()

    if not user:
        return {
            'status': 'error',
            'message': 'No user found with that id'
        }, 404

    # Check if the access token is current, and if not request a refreshed one
    if not user.spotify_token_is_current:
        response = get_new_spotify_token(refresh_token=user.spotify_refresh_token)

        if not response.ok:
            return {
                'status': 'error',
                'message': 'Error getting new token'
            }, 500

        user.spotify_token = response.json()['access_token']
        user.spotify_token_expires_at = datetime.now() + timedelta(seconds=response.json()['expires_in'])
        user.spotify_token_last_refreshed = datetime.now()
        user.api_calls = 0
        db.session.commit()

    return jsonify({
        'user': {
            'id': user.id,
            'spotify_token_expires_at': user.spotify_token_expires_at.isoformat(),
            'spotify_token_is_current': user.spotify_token_is_current,
            'server_time': datetime.now().isoformat(),
        },
        'data': get_recently_listened(user.spotify_token)
    })


#@app.route('/success')
#def success():
#    return render_template('success.html')


# Run the app
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5001, debug=True)
