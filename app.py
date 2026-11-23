from pathlib import Path
from datetime import datetime, timezone
import io, json, math, re, subprocess, sys
import pandas as pd
import streamlit as st
import altair as alt
from engine import endpoints, analyze, excel

st.set_page_config(page_title='Market Risk Lab',layout='wide')
st.title('Market Risk Lab')
st.write('Estimate stock betas, compare total volatility, and see how the required return changes with the model.')
with st.form('request'):
    names=st.text_area('U.S.-listed companies or tickers','NEM, AAPL, BA, CI, KO, JPM, MSFT, NVDA, TSLA, XOM, COST, CAT, DUK, AMT, NFLX',height=70)
    left,right=st.columns(2)
    rf=left.number_input('Risk-free rate assumption (%)',value=4.0,step=.25)
    premium=right.number_input('Market risk premium assumption (%)',value=5.0,step=.25)
    with st.expander('Price provider'):
        provider=st.radio('Adjusted prices',['Yahoo Finance','Tiingo'],horizontal=True)
        token=st.text_input('Tiingo API token (only for Tiingo)',type='password')
    run=st.form_submit_button('Analyze with fresh data',type='primary')
if run:
    st.session_state.pop('report',None)
    terms=list(dict.fromkeys(t.strip() for t in re.split('[,;\n]+',names) if t.strip()))
    if not 1<=len(terms)<=15: st.error('Enter 1–15 companies per comparison.');st.stop()
    if provider=='Tiingo' and not token:st.error('Enter your Tiingo token or select Yahoo Finance.');st.stop()
    with st.spinner('Fetching adjusted prices and published betas…'):
        moment=pd.Timestamp.now(tz='UTC')
        md,wd,last_close=endpoints(moment, include_latest=True)
        try:
            req=dict(terms=terms,start=min(md[0],wd[0]).date().isoformat(),end=last_close.date().isoformat(),token=token if provider=='Tiingo' else None)
            child=subprocess.run([sys.executable,str(Path(__file__).with_name('feed.py'))],input=json.dumps(req),text=True,capture_output=True,timeout=180)
            reply=json.loads(child.stdout.strip().splitlines()[-1])
            if 'error' in reply:raise ValueError(reply['error'])
            prices=pd.read_json(io.StringIO(reply['prices']),orient='split')
            prices.index=pd.to_datetime(prices.index)
            table,issues,series=analyze(prices,reply['stocks'],md,wd,rf,premium,reply['published'])
            for msg in reply['errors']+issues:st.warning(msg)
            if table.empty:st.error('No stock had complete, current history for every model.');st.stop()
            references = reply['citations']
            for label, field in [('Beta provider', 'source'), ('Beta source link', 'url'), ('Beta checked (UTC)', 'checked')]:
                table[label] = table.Ticker.map(lambda ticker: references[ticker][field])
            meta={'Requested (UTC)':moment.strftime('%Y-%m-%d %H:%M:%S'),'Prices':provider,'Published betas':'Per-stock provider, URL and retrieval time on Results; vendor windows may differ',
                  'Latest SPY price received':prices.SPY.last_valid_index().date().isoformat(),'Monthly through':str(md[-1].date()),'Weekly through':str(wd[-1].date()),
                  'Risk-free rate (%)':rf,'Market risk premium (%)':premium}
            st.session_state.report=(table,meta,excel(table,series,prices,meta),reply['errors']+issues)
        except (ValueError,IndexError,subprocess.TimeoutExpired) as err:
            st.error(str(err) if isinstance(err,ValueError) else 'The provider did not finish. Retry or select Tiingo.');st.stop()
if 'report' not in st.session_state:
    st.info('Click Analyze to load current data. The 4% and 5% inputs are editable scenarios.')
    st.stop()
table,meta,download,issues=st.session_state.report
st.subheader('Risk comparison')
st.caption(f"Requested {meta['Requested (UTC)']} UTC · {meta['Prices']} · Latest SPY close received {meta['Latest SPY price received']} · Monthly through {meta['Monthly through']} · Weekly through {meta['Weekly through']}")
if issues:st.caption('Excluded or unresolved: '+' | '.join(issues))
base=table[table.Method=='5Y monthly · SPY'].copy()
cols=st.columns(3)
cols[0].metric('Stocks analyzed',len(base))
volatile=base.loc[base['5Y volatility %'].idxmax()]
cols[1].metric('Highest total volatility',f"{volatile.Ticker} · {volatile['5Y volatility %']:.1f}%")
low=base.loc[base.Beta.idxmin()];cols[2].metric('Lowest market beta',f'{low.Ticker} · {low.Beta:.2f}')
plot=alt.Chart(base).encode(x=alt.X('Beta:Q',title='Market beta (five-year monthly, SPY)',scale=alt.Scale(zero=False)),y=alt.Y('5Y volatility %:Q',title='Annualized volatility (%)'),tooltip=['Ticker','Beta','5Y volatility %'])
st.altair_chart((plot.mark_circle(size=120,color='#086e72')+plot.mark_text(dy=-12).encode(text='Ticker')).properties(height=310),width='stretch')
case=base[base.Beta<1].sort_values('5Y volatility %',ascending=False)
if len(case):
    a=case.iloc[0];others=base[(base.Beta>a.Beta)&(base['5Y volatility %']<a['5Y volatility %'])].sort_values('Beta',ascending=False)
    if len(others):
        b=others.iloc[0];st.write(f"**{a.Ticker} and {b.Ticker} illustrate the Apex case.** {a.Ticker} had higher total volatility ({a['5Y volatility %']:.1f}% vs. {b['5Y volatility %']:.1f}%) and lower market beta ({a.Beta:.2f} vs. {b.Beta:.2f}).")
st.subheader('Estimated vs. published beta')
comparison = base[['Ticker','Beta','Published beta','Base − published','Beta provider','Beta source link','Beta checked (UTC)']]
st.dataframe(comparison.round(3), hide_index=True, width='stretch',
             column_config={'Beta': 'Estimated beta (5Y SPY)', 'Beta source link': st.column_config.LinkColumn('Source', display_text='View source')})
st.subheader('Model sensitivity')
st.caption(f"CAPM uses a {meta['Risk-free rate (%)']:g}% risk-free rate and a {meta['Market risk premium (%)']:g}% premium in every scenario. Changes are measured against the five-year monthly SPY estimate.")
selection=st.selectbox('Inspect a company',['All stocks']+base.Ticker.tolist())
view=table if selection=='All stocks' else table[table.Ticker==selection]
st.dataframe(view.round(3),hide_index=True,width='stretch')
if base['Published beta'].isna().any():st.caption('Both sources were checked for the tickers marked Unavailable. The source links let you inspect those observations.')
st.download_button('Download Excel analysis',download,'Market_Risk_Lab.xlsx',mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
with st.expander('Calculation details and sources'):
    st.write('Beta is the regression slope of adjusted stock returns on benchmark returns. Monthly volatility is sample standard deviation × √12. Each calculation uses the last exchange session of each completed period. Stocks missing a required endpoint are flagged.')
    st.write('The models use 60 monthly returns, 24 monthly returns, or 260 weekly returns. SPY tracks the S&P 500. QQQ tracks the Nasdaq-100. Holding the premium constant isolates the effect of substituting the benchmark.')
    st.write('The published beta is the current vendor snapshot. Its estimation window can differ. A historical beta and a CAPM scenario are estimates.')
    st.markdown('[Yahoo Finance](https://finance.yahoo.com/) · [Tiingo adjusted price documentation](https://www.tiingo.com/documentation/end-of-day)')
