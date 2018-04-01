import json

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.ext.hybrid import hybrid_property

import pandas as pd

from biorxiv_scraper import baseurl

db = SQLAlchemy()

class Biorxiv(db.Model):
    id              = db.Column(db.String, primary_key=True)
    created         = db.Column(db.DateTime)
    title           = db.Column(db.String)
    _parse_status   = db.Column('parse_status', db.Integer)
    _parse_data     = db.Column('parse_data', db.String)
    _author_contact = db.Column('author_contact', db.String)
    email_sent      = db.Column(db.Integer)

    @hybrid_property
    def parse_data(self):
        return pd.DataFrame.from_json(self._parse_data)

    @parse_data.setter
    def parse_data(self, df):
        self._parse_data = df.to_json()

    @hybrid_property
    def author_contact(self):
        return json.loads(self._author_contact)

    @author_contact.setter
    def author_contact(self, data):
        self._author_contact = json.dumps(data)

    @hybrid_property
    def url(self):
        return baseurl(self.id)

class Test(Biorxiv):
    pass
