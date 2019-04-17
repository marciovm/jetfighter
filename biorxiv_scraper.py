"""
    Utility functions for scraping biorxiv and processing preprint PDFs
"""

import re
import importlib
import PyPDF2
    
# neither urllib nor requests play nicely with rq
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from urllib.request import urlopen  
from bs4 import BeautifulSoup

def baseurl(code):
    return 'https://www.biorxiv.org/content/10.1101/{}'.format(code)

def req(url):
    http = urllib3.PoolManager()
    page = http.request('get', url, timeout=120)
    return page.data.decode('utf-8')

def find_authors(paper_id):
    """
    Retrieves page and captures author emails as list of strings
    """
    url = baseurl(paper_id) + '.article-info'
    page = req(url)
    soup = BeautifulSoup(page, 'lxml')
    re_at = re.compile('\{at\}')
    addr = soup(text=re_at)
    addr = [t.replace('{at}', '@') for t in addr]

    # corresponding authors will have their email listed in more than 1 place
    corr = list(set([x for x in addr if addr.count(x) > 1]))
    # if not, use the last author
    if not corr:
        corr = [addr[-1]]

    return dict(corr=corr, all=list(set(addr)))

def find_date(paper_id):
    """
    Visit bioRxiv and scrape the submission date
    """

    url = "https://www.biorxiv.org/content/10.1101/{}".format(paper_id)
    page = req(url)
    soup = BeautifulSoup(page, 'lxml')
    text = soup.find_all("meta", {"name": "DC.Date"})[0]
    return text.attrs['content']

def download_biorxiv_PDF_from_ID(paper_id):
    url = "https://www.biorxiv.org/content/10.1101/{}".format(paper_id)
    page = req(url)
    soup = BeautifulSoup(page, 'lxml')
    locate_PDF = soup.find_all('a', class_='article-dl-pdf-link')[0]['href']
    PDF_uri = 'https://biorxiv.org' + locate_PDF     
    # TODO: check if PDF already exists        
    response = urlopen(PDF_uri)
    file = open("./PDFs/" + paper_id + ".pdf", 'wb')
    file.write(response.read())
    file.close()
        
def count_pages(paper_id):
    """
    Find a local PDF downloaded from bioRxiv and count pages
    """
    reader = PyPDF2.PdfFileReader(open("./PDFs/" + paper_id + ".pdf", 'rb'))
    return reader.getNumPages()


# testing helpers
def test_count_pages():
    assert count_pages('515643v1') == 44

def test_find_date():
    find_date("515643v1") == "2019-01-13"

def test_find_authors():
    assert find_authors('121814') == \
        {'all': ['raul.peralta@uaem.mx'], 'corr': ['raul.peralta@uaem.mx']}
