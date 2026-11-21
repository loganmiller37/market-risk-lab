"""A fresh process per analysis keeps provider caches from spanning runs."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json, math, re, sys
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from curl_cffi import requests as browser_requests
import pandas as pd
import requests
import yfinance as yf

def resolve(term, directory):
    term=term.strip()
    if term.upper() in directory: return term.upper()
    matches=[t for t,name in directory.items() if term.casefold()==name.casefold()]
    if len(matches)==1: return matches[0]
    if re.fullmatch(r'[A-Z]{1,6}([.-][A-Z])?',term) or (len(term)<=5 and term.isalpha() and term.islower() and not any(term.casefold() in n.casefold() for n in directory.values())):
        return term.upper().replace('.','-')
    matches=[t for t,name in directory.items() if term.casefold() in name.casefold()]
    if len(matches)==1:return matches[0]
    if len(matches)>1:raise ValueError("Several companies match. Enter a ticker: "+", ".join(matches[:8]))
    quotes=yf.Search(term,max_results=8,news_count=0).quotes
    choices=list(dict.fromkeys(q['symbol'] for q in quotes if q.get('quoteType')=='EQUITY' and q.get('exchange') in ('NYQ','NMS','NGM','NCM','ASE','PCX','BTS','NYE')))
    if len(choices)==1:return choices[0]
    if choices:raise ValueError("Choose a ticker: "+", ".join(choices))
    raise ValueError("Enter the U.S. ticker symbol.")

def stock_analysis_beta(url):
    response = browser_requests.get(url, impersonate="chrome", timeout=10)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    for row in soup.select("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) == 2 and cells[0].get_text(strip=True) == "Beta (5Y)":
            return float(cells[1].get_text(strip=True).replace("−", "-"))
    raise ValueError("Published five-year beta missing")


def published_beta(ticker):
    yahoo_url = f"https://finance.yahoo.com/quote/{ticker}/key-statistics/"
    stats_url = f"https://stockanalysis.com/stocks/{ticker.lower()}/statistics/"
    for source, url in [("Yahoo Finance", yahoo_url), ("Stock Analysis", stats_url)]:
        try:
            value = (float(yf.Ticker(ticker).get_info()["beta"])
                     if source == "Yahoo Finance" else stock_analysis_beta(url))
            if math.isfinite(value):
                break
        except Exception:
            pass
    else:
        value, source, url = None, "Unavailable", stats_url
    return ticker, {"value": value, "source": source, "url": url,
                    "checked": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")}


def fetch(req):
    directory=json.loads(Path(__file__).with_name('names.json').read_text())
    stocks=[]; errors=[]
    for term in req['terms']:
        try:
            t=resolve(term,directory)
            if t in ('SPY','QQQ'):raise ValueError("Reserved benchmark.")
            if t not in stocks:stocks.append(t)
        except Exception as exc:
            errors.append(term+" — "+(str(exc) if isinstance(exc,ValueError) else "Name search unavailable. Enter the ticker."))
    if not stocks:raise ValueError(' '.join(errors))
    syms=stocks+['SPY','QQQ']; start=req['start'];end=req['end']; token=req.get('token')
    if token:
        def tiingo(t):
            result=requests.get('https://api.tiingo.com/tiingo/daily/'+t+'/prices',
                params={'startDate':start,'endDate':end},headers={'Authorization':'Token '+token},timeout=20)
            if result.status_code!=200:return pd.Series(dtype=float,name=t)
            data=pd.DataFrame(result.json())
            if data.empty:return pd.Series(dtype=float,name=t)
            return pd.Series(data.adjClose.to_numpy(),index=pd.to_datetime(data.date,utc=True).dt.tz_localize(None),name=t)
        with ThreadPoolExecutor(4) as pool:prices=pd.concat(list(pool.map(tiingo,syms)),axis=1)
    else:
        raw=yf.download(syms,start=start,end=(pd.Timestamp(end)+pd.Timedelta(days=1)).date().isoformat(),
              auto_adjust=False,progress=False,threads=4,timeout=12,group_by='column')
        if raw.empty or 'Adj Close' not in raw:raise ValueError("Yahoo did not return adjusted prices. Retry or select Tiingo.")
        prices=raw['Adj Close']
    if any(t not in prices or prices[t].dropna().empty for t in ['SPY','QQQ']):
        raise ValueError("Benchmark prices unavailable. Check the provider and retry.")
    with ThreadPoolExecutor(4) as pool:
        citations = dict(pool.map(published_beta, stocks))
    published = {ticker: rec["value"] for ticker, rec in citations.items()}
    prices.index=pd.to_datetime(prices.index).tz_localize(None)
    return dict(stocks=stocks,errors=errors,published=published,citations=citations,prices=prices.to_json(orient='split',date_format='iso',double_precision=15))

if __name__=='__main__':
    try:print(json.dumps(fetch(json.load(sys.stdin))))
    except Exception as exc:
        print(json.dumps({'error':str(exc) if isinstance(exc,ValueError) else "Provider request failed. Retry or switch price source."}))
