"""Web app and utilities for rainbow colormap monitor
"""

import os
import os.path
import re
import math
import tempfile
import itertools
import base64

import gevent.monkey
gevent.monkey.patch_all()

import flask
from flask import Flask
from flask_rq2 import RQ
from flask_mail import Mail, Message
from flask_wtf.csrf import CSRFProtect, CSRFError

from sqlalchemy import desc

import click
import tweepy

import pytest

try:
    from PyPDF2 import PdfFileReader
except:
    print('Calculations will fail if this is a worker')

from tqdm import tqdm

from models import db, Biorxiv, Test
from biorxiv_scraper import find_authors, download_paper
from detect_cmap import convert_to_img, detect_rainbow_from_file

import utils

# Reads env file into environment, if found
_ = utils.read_env()

app = Flask(__name__)
app.config['BASE_URL'] = os.environ['BASE_URL']

# For data storage
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['SQLALCHEMY_DATABASE_URI']
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = True
db.init_app(app)

# For job handling
app.config['RQ_REDIS_URL'] = os.environ['RQ_REDIS_URL']
rq = RQ(app)

# For monitoring papers (until Biorxiv provides a real API)
app.config['TWITTER_APP_KEY'] = os.environ['TWITTER_APP_KEY']
app.config['TWITTER_APP_SECRET'] = os.environ['TWITTER_APP_SECRET']
app.config['TWITTER_KEY'] = os.environ['TWITTER_KEY']
app.config['TWITTER_SECRET'] = os.environ['TWITTER_SECRET']

# for author notification
app.config['MAIL_SERVER'] = os.environ['MAIL_SERVER']
app.config['MAIL_PORT'] = int(os.environ['MAIL_PORT'])
app.config['MAIL_USE_TLS'] = bool(int(os.environ['MAIL_USE_TLS']))
app.config['MAIL_USERNAME'] = os.environ['MAIL_USERNAME']
app.config['MAIL_PASSWORD'] = os.environ['MAIL_PASSWORD']
app.config['MAIL_DEFAULT_SENDER'] = os.environ['MAIL_DEFAULT_SENDER'].replace("'", "")
app.config['MAIL_REPLY_TO'] = os.environ['MAIL_REPLY_TO'].replace("'", "")
app.config['MAIL_MAX_EMAILS'] = int(os.environ['MAIL_MAX_EMAILS'])

app.config['DEBUG'] = os.environ.get('DEBUG', 0)

app.config['WEB_PASSWORD'] = os.environ['WEB_PASSWORD']

app.config['SECRET_KEY'] = os.environ['SECRET_KEY']
csrf = CSRFProtect(app)

tweepy_auth = tweepy.OAuthHandler(
    app.config['TWITTER_APP_KEY'], app.config['TWITTER_APP_SECRET'])
tweepy_auth.set_access_token(
    app.config['TWITTER_KEY'], app.config['TWITTER_SECRET'])
tweepy_api = tweepy.API(tweepy_auth)

mail = Mail(app)

@app.route('/')
def home():
    """Renders the website with current results
    """

    cats = flask.request.args.get('categories')
    if cats:
        cats = [int(x) for x in cats.split(',')]
    else:
        cats = [-2, -1, 1, 2]

    papers = (Biorxiv.query
                     .filter(Biorxiv.parse_status.in_(cats))
                     .order_by(desc(Biorxiv.created))
                     .limit(500)
                     .all())

    return flask.render_template('main.html', app=app, papers=papers)

@app.route('/pages/<string:paper_id>')
def pages(paper_id, prepost=1, maxshow=10):
    """Returns base64 encoded image intended for ajax calls only
    1-index I think...
    """
    record = Biorxiv.query.filter_by(id=paper_id).first()
    if not record:
        return flask.jsonify({})

    pages = record.pages
    page_count = record.page_count

    # find pages before and after (requested or default)
    try:
        prepost = math.fabs(int(flask.request.args.get('prepost')))
    except:
        pass

    # find pages before and after (requested or default)
    try:
        maxshow = math.fabs(int(flask.request.args.get('maxshow')))
    except:
        pass

    # if requested, show all pages with each page's status
    try:
        all_pages = math.fabs(int(flask.request.args.get('all')))
        if int(all_pages) == 1:
            show_pgs = {i:True for i in pages[:maxshow]}
            for i in range(page_count):
                if i not in show_pgs:
                    show_pgs[i] = False
            return flask.jsonify(show_pgs)
    except:
        pass

    show_pgs = {}
    if pages:
        # add all detected pages up to maxshow count
        show_pgs = {i:True for i in pages[:maxshow]}
        # pad with undetected pages
        for i in pages:
            for j in range(i - prepost, min(i + prepost + 1, page_count)):
                if len(show_pgs) < maxshow:
                    if j not in pages:
                        show_pgs[j] = False
                else:
                    return flask.jsonify(show_pgs)
    else:
        show_pgs = {i:False for i in range(1, maxshow + 1)}

    return flask.jsonify(show_pgs)

@app.route('/preview/<string:paper_id>/<int:pg>')
def preview(paper_id, pg):
    """Returns base64 encoded image intended for ajax calls only
    """
    img_data = None

    # download and convert paper to images
    # delete those pages that aren't shown
    pdf_fn = "static/previews/{}.pdf".format(paper_id)
    if not os.path.exists(pdf_fn):
        pdf_fn = download_paper(paper_id, "static/previews/")

    for pg_fn in convert_to_img(pdf_fn, outdir='static/previews/', format='jpeg',
        other_opt=['-f', str(pg), '-l', str(pg), '-jpegopt', 'quality=50,progressive=y', '-scale-to', '350']):
        if re.match(".*\-0*{}".format(pg), pg_fn):
            with open(pg_fn, "rb") as fh:
                img_data = fh.read()
            break

    b64img = 'data:image/jpg;base64,' + base64.b64encode(img_data).decode("utf-8")

    return flask.jsonify(b64img)
# flask.render_template('preview_img.html', b64img=b64img)

@app.route('/detail/<string:paper_id>')
def show_details(paper_id, prepost=1, maxshow=10):
    """
    """
    record = Biorxiv.query.filter_by(id=paper_id).first()
    if not record:
        flask.flash('Sorry! Results with that ID have not been found')
        return flask.redirect('/')

    # Format colormap for viewing
    df_cm = record.parse_data
    if df_cm.size > 0:
        df_cm['fn'] = df_cm['fn'].str.split('-', n=1).str[1]
        df_cm['pct_cm'] = df_cm['pct_cm'] * 100
        df_cm['pct_page'] = df_cm['pct_page'] * 100
    df_cm = df_cm[['cm', 'fn', 'pct_cm', 'pct_page']]
    df_cm.rename(columns={
        'fn': 'Page',
        'cm': 'Colormap Abbreviation',
        'pct_cm': 'Colormap Coverage (%)',
        'pct_page': 'Page Coverage (%)',
    }, inplace=True)

    cm_table = df_cm.to_html(bold_rows=False, index=False, border=0,
        table_id="cm_parse_table", float_format='%.2f')

    # display images
    return flask.render_template('detail.html',
        paper_id=record.id, title=record.title, url=record.url,
        pages=", ".join([str(p) for p in record.pages]),
        parse_status=record.parse_status, email_sent=record.email_sent,
        cm_parse_html=cm_table
        )

@app.route('/notify/<string:paper_id>', methods=['POST'])
@app.route('/notify/<string:paper_id>/<int:force>', methods=['POST'])
def notify_authors(paper_id, force=0):
    if flask.session.get('logged_in'):
        record = Biorxiv.query.filter_by(id=paper_id).first()
        if not record:
            return flask.jsonify(result=False, message="paper not found")

        addr = record.author_contact.values()
        addr = list(itertools.chain.from_iterable(addr))
        # don't bother everyone if there are a ton of authors
        if len(addr) > 6:
            addr = [*addr[:2], *addr[-3:]]

        if addr is [] or '@' not in "".join(addr):
            return flask.jsonify(result=False,
                message="mangled or missing email addresses")

        if force != 1 and record.email_sent == 1:
            return flask.jsonify(result=False,
                message="email already sent. use the cli to send another")

        msg = Message(
            "[JetFighter] bioRxiv manuscript {}".format(record.id),
            recipients=addr,
            reply_to=app.config['MAIL_REPLY_TO'])
        msg.body = flask.render_template("email_notification.txt",
            paper_id=paper_id,
            pages=record.pages_str,
            title=record.title,
            detail_url=flask.url_for('show_details', paper_id=paper_id))
        mail.send(msg)

        record.email_sent = 1
        db.session.merge(record)
        db.session.commit()

        return flask.jsonify(result=True, message="successfully sent")
    else:
        return flask.jsonify(result=False, message="not logged in")

@app.route('/toggle/<string:paper_id>', methods=['POST'])
def toggle_status(paper_id):
    if flask.session.get('logged_in'):
        record = Biorxiv.query.filter_by(id=paper_id).first()
        if not record:
            return flask.jsonify(result=False, message="paper not found")
        if record.parse_status > 0:
            record.parse_status = -2
        elif record.parse_status < 0:
            record.parse_status = 2
        else:
            return flask.jsonify(result=False, message="Not yet parsed")
        db.session.merge(record)
        db.session.commit()

        return flask.jsonify(result=True, message="successfully changed")
    else:
        return flask.jsonify(result=False, message="not logged in")

@app.route('/rerun', methods=['GET', 'POST'])
@app.route('/rerun/<string:paper_id>', methods=['POST'])
def rerun_web(paper_id=None):
    """Requeue jobs from the web interface

    If only a single paper, then do this synchronously.
    If all (i.e. paper_id == None), then do this on the queue
    to avoid delaying the redirect.

    """
    if flask.session.get('logged_in'):
        if paper_id is None:
            _rerun.queue(paper_id)
            flask.flash("Rerun job has been queued")
            return flask.redirect('/')
        else:
            n_queue = _rerun(paper_id)
            if n_queue == -1:
                message = "Paper not found"
            else:
                message = "Paper has been queued"

            if flask.request.method == 'GET':
                flask.flash(message)
                return flask.redirect('/')
            return flask.jsonify(result=n_queue != -1, message=message)
    else:
        flask.flash("Not logged in")
        return flask.redirect('/login')

@app.route('/login', methods=['GET', 'POST'])
def admin_login():
    if flask.request.method == 'GET':
        if flask.session.get('logged_in'):
            flask.flash('You are already logged in!')
            return flask.redirect('/')
        return flask.render_template('login.html')

    if flask.request.form['password'] == app.config['WEB_PASSWORD']:
        flask.session['logged_in'] = True
    else:
        flask.flash('wrong password!')
        return flask.render_template('login.html')

    return flask.redirect('/')

@app.route('/logout', methods=['GET'])
def logout():
    logged_in = flask.session.get('logged_in')
    flask.session.clear()
    if logged_in:
        flask.flash("You have been successfully logged out")
    else:
        flask.flash("Not logged in! (but the session has been cleared)")
    return flask.redirect('/')

@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    flask.flash('CSRF Error. Try again?')
    return flask.redirect(flask.url_for('admin_login'))


def parse_tweet(t, db=db, objclass=Biorxiv, verbose=True):
    """Parses tweets for relevant data,
       writes each paper to the database,
       dispatches a processing job to the processing queue (rq)
    """
    try:
        text = t.extended_tweet["full_text"]
    except AttributeError:
        pass

    try:
        text = t.full_text
    except AttributeError:
        text = t.text

    if verbose:
        print(t.id_str, text[:25], end='\r')
    if not db:
        return

    try:
        url = t.entities['urls'][0]['expanded_url']
        code = os.path.basename(url)
    except:
        print('Error parsing url/code from tweet_id', t.id_str)
        return

    try:
        title = re.findall('(.*?)\shttp', text)[0]
    except:
        # keep ASCII only (happens with some Test tweets)
        title = re.sub(r'[^\x00-\x7f]', r'', text)

    obj = objclass(
        id=code,
        created=t.created_at,
        title=title,
    )

    obj = db.session.merge(obj)
    db.session.commit()

    # don't reprocess papers that have already been entered
    if obj.title is None:
        process_paper.queue(obj)


@app.cli.command()
def retrieve_timeline():
    """Picks up current timeline (for testing)
    """
    for t in tweepy_api.user_timeline(screen_name='biorxivpreprint', count=1000,
            trim_user='True', include_entities=True, tweet_mode='extended'):
        parse_tweet(t)

def page_count(fn):
    with open(fn, 'rb') as fh:
        return PdfFileReader(fh).getNumPages()

@rq.job(timeout='30m')
def process_paper(obj):
    """Processes paper starting from url/code

    1. download paper
    2. check for rainbow colormap
    3. if rainbow, get authors
    4. update database entry with colormap detection and author info
    """
    obj = db.session.merge(obj)
    with tempfile.TemporaryDirectory() as td:
        fn = download_paper(obj.id, outdir=td)
        obj.pages, obj.parse_data = detect_rainbow_from_file(fn)
        obj.page_count = page_count(fn)
        if len(obj.pages) > 0:
            obj.parse_status = 1
            obj.author_contact = find_authors(obj.id)
        else:
            obj.parse_status = -1
        db.session.merge(obj)
        db.session.commit()


## NOTE: NEEDS WORK
@pytest.fixture()
def test_setup_cleanup():
    # should only be one, but... just in case
    for obj in Test.query.filter_by(id='172627v1').all():
        db.session.delete(obj)
    db.session.commit()

    # Delete temporary row
    for obj in Test.query.filter_by(id='172627v1').all():
        db.session.delete(obj)
    db.session.commit()

def test_integration(test_setup_cleanup):
    """Submit job for known jet colormap. Remove from database beforehand.
    Write to database.
    Check for written authors.
    """

    testq = rq.Queue('testq', async=False)

    preobj = Test(id='172627v1')
    testq.enqueue(process_paper, preobj)

    postobj = Test.query.filter_by(id='172627v1').first()

    # check that document was correctly identified as having a rainbow colormap
    assert postobj.parse_status

    # check that authors were correctly retrieved
    authors = postobj.author_contact
    assert authors['corr'] == ['t.ellis@imperial.ac.uk']
    assert set(authors['all']) == set([
        'o.borkowski@imperial.ac.uk', 'carlos.bricio@gmail.com',
        'g.stan@imperial.ac.uk', 't.ellis@imperial.ac.uk'])

@rq.job()
def _rerun(paper_id=None):
    """Rerun some or all papers in database
    """
    n_queue = 0
    if paper_id:
        rec = Biorxiv.query.filter_by(id=paper_id).first()
        if rec:
            process_paper.queue(rec)
            n_queue += 1
        else:
            return -1
    else:
        for rec in Biorxiv.query.filter(
                        Biorxiv.parse_status.in_([-1, 0, 1])
                        ).all():
            process_paper.queue(rec)
            n_queue += 1
    return n_queue


@app.cli.command()
@click.argument('paper_ids', nargs=-1, default=None, required=False)
@click.option('--all', is_flag=True)
@click.option('--now', is_flag=True)
def rerun(paper_ids, all=False, now=False):
    """Rerun some or all papers in database
    """
    n_queue = 0

    if all:
        for rec in tqdm(Biorxiv.query.all()):
            process_paper.queue(rec)
    else:
        if paper_ids and len(paper_ids) > 0:
            for p in paper_ids:
                if now:
                    rec = Biorxiv.query.filter_by(id=p).first()
                    process_paper(rec)
                else:
                    if _rerun(p) == -1:
                        print("paper_id {} not found".format(p))
                    else:
                        n_queue += 1
        else:
            n_queue = _rerun()

    print("Queued {} jobs".format(n_queue))

    return


if __name__ == "__main__":
    app.run(debug=True, threaded=True, use_reloader=False)
