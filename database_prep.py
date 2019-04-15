from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from models import db, Biorxiv, Test

app = flask.Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['SQLALCHEMY_DATABASE_URI']
db.init_app(app)
migrate = Migrate(app, db)

class Biorxiv(db.Model):
    source          = db.Column(db.String(10), default='biorxiv')
    id              = db.Column(db.String, primary_key=True)
    created         = db.Column(db.DateTime)
    title           = db.Column(db.String)
    parse_status    = db.Column(db.Integer, default=0, nullable=False)
    _parse_data     = db.Column('parse_data', db.String)
    _pages          = db.Column('pages', db.String, default='[]', nullable=False)
    page_count      = db.Column('page_count', db.Integer, default=0, nullable=False)
    posted_date     = db.Column(db.String(10), default='')
    _author_contact = db.Column('author_contact', db.String)
    email_sent      = db.Column(db.Integer)
