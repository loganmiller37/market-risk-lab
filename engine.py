import io
import numpy as np
import pandas as pd
import pandas_market_calendars as calendars


def endpoints(now, include_latest=False):
    now = pd.Timestamp(now).tz_convert("UTC")
    today = now.tz_convert("America/New_York").tz_localize(None).normalize()
    schedule = calendars.get_calendar("NYSE").schedule(
        start_date=today - pd.DateOffset(years=5, months=3),
        end_date=today + pd.DateOffset(months=2))
    def completed(freq, count):
        ends = schedule.groupby(schedule.index.to_period(freq)).tail(1)
        return ends.index[ends.market_close <= now][-count:]
    periods = completed("M", 61), completed("W-FRI", 261)
    return (*periods, schedule.index[schedule.market_close <= now][-1]) if include_latest else periods


def analyze(prices, stocks, monthly, weekly, rf, premium, published):
    results, errors, series = [], [], {}
    def period_returns(dates, ticker):
        sample = prices.reindex(dates)[[ticker, "SPY", "QQQ"]]
        bad = (~np.isfinite(sample) | (sample <= 0)).any(axis=1)
        if bad.any():
            raise ValueError(f"Missing adjusted close on {sample.index[bad][-1]:%b %d, %Y}. Full history required.")
        return sample.pct_change(fill_method=None).iloc[1:]
    for t in stocks:
        try:
            if t not in prices: raise ValueError("No price history returned.")
            m, w = period_returns(monthly,t), period_returns(weekly,t)
            series[t] = (m,w)
            base = None
            for method, data, benchmark in [('5Y monthly · SPY',m,'SPY'),('2Y monthly · SPY',m.tail(24),'SPY'),
                     ('5Y weekly · SPY',w,'SPY'),('5Y monthly · QQQ',m,'QQQ')]:
                x, y = data[benchmark], data[t]
                beta = y.cov(x)/x.var()
                if base is None: base = beta
                pb=published.get(t)
                results.append({'Ticker':t,'Method':method,'Returns':len(data),'Beta':beta,'Δ beta':beta-base,
                    'CAPM %':rf+premium*beta,'Δ CAPM (pp)':premium*(beta-base),
                    '5Y volatility %':100*m[t].std()*np.sqrt(12),'Published beta':pb,
                    'Base − published':None if pb is None else base-pb})
        except ValueError as exc: errors.append(f"{t} — {exc}")
    return pd.DataFrame(results), errors, series


def excel(table, series, prices, metadata):
    output=io.BytesIO()
    with pd.ExcelWriter(output,engine="xlsxwriter") as writer:
        table.to_excel(writer,sheet_name="Results",index=False)
        pd.Series(metadata,name="Value").rename_axis("Item").to_excel(writer,sheet_name="Run details")
        prices.to_excel(writer,sheet_name="Adjusted close",index_label="Date")
        for i,title in enumerate(["Monthly returns","Weekly returns"]):
            frame=pd.concat([pairs[i][ticker] for ticker,pairs in series.items()],axis=1)
            pair=next(iter(series.values()))[i]
            frame.join(pair[['SPY','QQQ']]).to_excel(writer,sheet_name=title,index_label="Date")
        from xlsxwriter.utility import xl_col_to_name
        result_sheet=writer.sheets['Results']
        detail_sheet=writer.sheets['Run details']
        rate_row=list(metadata).index('Risk-free rate (%)')+2
        premium_row=list(metadata).index('Market risk premium (%)')+2
        for row in [rate_row,premium_row]:
            detail_sheet.write_number(row-1,1,float(list(metadata.values())[row-2]))
        symbols=list(series)
        for index, item in table.iterrows():
            pos=symbols.index(item['Ticker'])
            stock_col=xl_col_to_name(pos+1)
            market_col=xl_col_to_name(len(symbols)+(2 if 'QQQ' in item.Method else 1))
            weekly='weekly' in item.Method
            sheet='Weekly returns' if weekly else 'Monthly returns'
            end=len(series[item.Ticker][int(weekly)])+1
            start=end-int(item.Returns)+1
            row=index+2
            base_row=2+4*pos
            formulas={'Beta':f"=SLOPE('{sheet}'!{stock_col}{start}:{stock_col}{end},'{sheet}'!{market_col}{start}:{market_col}{end})",
                      'Δ beta':f'=D{row}-D{base_row}',
                      'CAPM %':f"='Run details'!$B${rate_row}+D{row}*'Run details'!$B${premium_row}",
                      'Δ CAPM (pp)':f'=F{row}-F{base_row}',
                      '5Y volatility %':f"=100*STDEV('Monthly returns'!{stock_col}2:{stock_col}61)*SQRT(12)"}
            if pd.notna(item['Published beta']):formulas['Base − published']=f'=D{base_row}-I{row}'
            for label,formula in formulas.items():
                result_sheet.write_formula(row-1,table.columns.get_loc(label),formula,None,float(item[label]))
        for sheet in writer.sheets.values():
            sheet.freeze_panes(1,1);sheet.set_column(0,20,21)
        writer.sheets["Run details"].set_column(1,1,75)
    return output.getvalue()
