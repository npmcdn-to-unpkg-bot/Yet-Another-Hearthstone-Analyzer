import sys
import json
import requests
import pandas as pd
import numpy as np
import os.path
import datetime
import sqlite3
import hashlib


DATA_PATH = "/"

HS_JSON = "https://api.hearthstonejson.com/v1/latest/enUS/"
HS_JSON_EXT = ["cardbacks.json", "cards.collectible.json", "cards.json"]

USERNAME = "ancient-molten-giant-2943"
API_KEY = "-X_VZRijrHoV4qMZxfXq"
URL = "https://trackobot.com/profile/history.json?"

class preordain_analyzer(object):

    def __init__(self):
        self.total_pages = 0
        self.history = []
        self.username = ""
        self.api_key = ""
        self.new_data = False

    def grab_data(self, username, api_key):
        """
        Use this method, pull_data and parse_data are both outdated and require mulitude of files
        """
        self.username = username
        self.api_key = api_key
        url = "https://trackobot.com/profile/history.json?"
        auth = {"username": username, "token": api_key}
        req = requests.get(url, params=auth).json()
        metadata = req["meta"]
        count, json_name, hdf5name = self.store_data()
        #if it's not equal, repull
        if metadata['total_items'] != count:
            results = {'children': req['history']}
            if metadata['total_pages'] != None:
                for page_number in range(2, metadata['total_pages']+1):
                    auth['page'] = page_number
                results['children'].extend(requests.get(url, params=auth).json()['history'])
            results['meta'] = {'total_items': metadata['total_items']}
            self.history = results
            self.generate_decks()
            self.write_hdf5(hdf5_name)
        else:
            results = self.read_data(json_name, hdf5_name)
        return results

    def generate_decks(self):
        """
        Differentiates between the different deck types, and sorts them into their individual lists (history is a massive array, transform into a pandas dataframe for processing)
        """
        self.games = pd.DataFrame(self.history['children'])
        self.games.loc[self.games['hero_deck'].isnull(), 'hero_deck'] = 'Other'
        self.games.loc[self.games['opponent_deck'].isnull(), 'opponent_deck'] = 'Other'
        self.games["p_deck_type"] = self.games["hero_deck"].map(str) + "_" +  self.games["hero"]
        self.games["o_deck_type"] = self.games["opponent_deck"].map(str) + "_" + self.games["opponent"]

        self._generate_cards_played()
        self._make_dates()
        return self.games


    def _make_dates(self):
        format_date = lambda x: datetime.datetime.strptime(x[:-5], '%Y-%m-%dT%H:%M:S')
        split_date = lambda x: {'year': x.year, 'month': x.month, 'day': x.day, 'hour': x.hour, 'minute': x.minute, 'second': x.second}
        date_df = pd.DataFrame(list(map(lambda x: split_date(format_date(x)), self.gaes['added'])))
        self.games = self.games.join(date_df, how='outer')

    def _get_card_list(self, dict_list, player='me'):
        """
        Returns the list of cards that were played in a game
        """
        p_card_list = list(filter(None, map(lambda x: x['card']['name'] if x['player'] == player else None, dict_list)))
        return p_card_list


    def _generate_cards_played(self):
        """
        Generates a list of cards for player and opponent into the list ['p_cards_played'] and ['o_cards_played'], meant to be used with the generate_cards function, this function is automatically called by generate_decks
        """
        self.games['p_cards_played'] = self.games['card_history'].map(lambda x: self._get_card_list(x, player='me'))
        self.games['o_cards_played'] = self.games['card_history'].map(lambda x: self._get_card_list(x, player='opponent'))

    def generate_matchups(self, game_mode = 'ranked', game_threshold = 0):
        """
        Generates a pandas groupby table with duration, count, coin, win #, and win %
        game_mode is either ranked, casual, or both
        game_threshold filters out the games with lower threshold
        """
        decks = self.games
        if game_mode != 'both':
            decks = decks[decks['mode'] == game_mode]
        decks['win'] = decks['result'].map(lambda x: True if x == 'win' else False)
        decks['count'] = [1]*len(decks)

        grouped = decks.groupby(["p_deck_type", "o_deck_type"]).agg({"coin": np.sum, "duration": [np.mean, np.std], "count": np.sum, "win": np.sum, "card_history": lambda x: tuple(x)})
        grouped['win%'] = grouped['win']['sum']/grouped['count']['sum']
        grouped = grouped[grouped['count']['sum'] > game_threshold]
        return grouped #note this returns a groupby, so a reset_index is necessary before pivoting/plotting

    def generate_cards(self, filtered):
        """
        Generates a grouped win/loss count for specific cards
        filtered should be a filtered subset of self.games, using some combination of rank/deck/hero
        """
        p_df = []
        o_df = []
        for r in zip(filtered['p_cards_played'], filtered['o_cards_played'], filtered['result']):
            for p_card in r[0]:
                p_df.append({'card': p_card, 'win': 1, 'loss': 0} if r[2] == 'win' else {'card': p_card, 'win': 0, 'loss': 1})
            for o_card in r[1]:
                o_df.append({'card': o_card, 'win': 1, 'loss': 0} if r[2] == 'loss' else {'card': o_card, 'win': 0, 'loss': 1})

        p_df = pd.DataFrame(p_df)
        o_df = pd.DataFrame(o_df)
        p_df = p_df.groupby('card').agg(np.sum)
        o_df = o_df.groupby('card').agg(np.sum)
        return p_df, o_df

    def write_hdf5(self, hdf5_name):
        """
        Writes out self.games into a hdf5_file
        """
        self.games.to_hdf('{}{}'.format(DATA_PATH, hdf5_name), 'table', append = False)

    def store_data(self):
        """
        Stores the python data by using the filename as the sha5 hash of the username and api_key -> hash is stored in a database for lookups later, data is stored using the hdf5 format
        Table is in the format of ['user_hash', 'total_items', 'json_name', 'hdf5_name']
        """

        user_hash = hashlib.sha1(b'{}{}'.format(self.username, self.api_key)).hexdigest()
        conn = sqlite3.connect('~/userdata.db')
        c = conn.cursor()
        if c.execute("SELECT (1) from users where user_hash=?", user_hash):
            c.execute('SELECT * FROM users WHERE user_hash=?', user_hash)
            user = c.fetchone()
        else:
            user = (user_hash, history['meta']['total_items'], '{}_j.json'.format(user_hash), '{{}_h.hdf5}'.format(user_hash))
            c.execute('INSERT INTO users VALUES ?', user)
        conn.commit()
        conn.close()
        return user[1], user[2], user[3]

    def read_data(self, json_name, hdf5_name):
        """
        Takes the names of the files and loads them into memory for processing
        """
        results = json.load("{}{}".format(DATA_PATH, json_name))
        self.history = results
        self.games = read_hdf("{}{}".format(DATA_PATH, hdf5_name), 'table')
        return results
