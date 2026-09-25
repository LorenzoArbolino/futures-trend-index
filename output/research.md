# Research notes

*Generated 2026-09-25 by `python run.py research`; every number is recomputed from the data on each run. Sharpe ratios are annualised from monthly returns over cash. Intervals are 95%, from a 12-month block bootstrap.*

## 1. Free data can manufacture a trend-following track record

Several free datasets publish the AVERAGE of each month's prices, not the month-end price (the World Bank Pink Sheet; BIS and ECB series by default). The average of month t already contains the second half of month t, so average-to-average returns are autocorrelated by construction (Working, 1960), and a trend rule harvests that autocorrelation as if it were a trend.

On **pure random walks** a 25-market 12-month trend index shows a Sharpe ratio of **1.43** on monthly averages (5th-95th percentile 1.07 to 1.78) and about zero on month-end prices. Lagging the signal one month removes the effect.

| Data | Monthly price | Signal lag | Sharpe | Range / period |
|---|---|---|---|---|
| Random walks, 25 markets x 25 years (200 histories) | month-end | none | 0.01 | -0.35 to 0.35 |
| Random walks, 25 markets x 25 years (200 histories) | monthly average | none | 1.43 | 1.07 to 1.78 |
| Random walks, 25 markets x 25 years (200 histories) | monthly average | +1 month | 0.02 | -0.41 to 0.37 |
| 21 commodity futures, daily data | month-end | none | 0.38 | 1991-01 to 2024-03 |
| 21 commodity futures, daily data | month-end | +1 month | 0.44 | 1991-02 to 2024-03 |
| 21 commodity futures, daily data | monthly average | none | 1.05 | 1991-01 to 2024-03 |
| 21 commodity futures, daily data | monthly average | +1 month | 0.43 | 1991-02 to 2024-03 |

World Bank averages are also misdated. Correlation of each World Bank monthly return with the true futures return of the same month, of the NEXT month's World Bank return with it, and of a free month-end source (Yahoo front month) for comparison:

| Market | World Bank, same month | World Bank, next month | Yahoo month-end |
|---|---|---|---|
| WTI crude | 0.71 | 0.45 | 0.89 |
| Brent crude | 0.78 | 0.51 | 0.96 |
| Natural gas | 0.51 | 0.44 | 0.94 |
| Gold | 0.58 | 0.55 | 0.99 |
| Silver | 0.63 | 0.54 | 0.97 |
| Platinum | 0.62 | 0.59 | 0.87 |
| Copper | 0.66 | 0.61 | 0.99 |
| Corn | 0.54 | 0.53 | 0.89 |
| Wheat (SRW) | 0.53 | 0.42 | 0.92 |
| Wheat (HRW) | 0.62 | 0.48 | 0.96 |
| Soybeans | 0.44 | 0.53 | 0.89 |
| Soybean oil | 0.46 | 0.52 | 0.95 |
| Soybean meal | 0.48 | 0.57 | 0.90 |
| Sugar | 0.64 | 0.53 | 0.89 |
| Cotton | 0.45 | 0.66 | 0.90 |
| Coffee | 0.58 | 0.52 | 0.99 |
| Cocoa | 0.56 | 0.54 | 0.97 |

## 2. Building the index one choice at a time

Each step adds one choice to the step above; alternatives change one thing in the final index. Net of costs, 1991-01 to 2026-08. The change columns give the difference in Sharpe ratio against the previous step (for alternatives: against the index), with a paired 95% interval, before 2008 and from 2008.

The choices come from the trend-following literature and from first principles (README, *Why it is built this way*). The design was refined while looking at the whole sample, so the period from 2008 is a pseudo out-of-sample check, not a clean holdout. Read the intervals accordingly: most individual choices cannot be distinguished from zero on their own; the case for them is the prior reasoning plus the consistency of the direction.

| Step | Sharpe before 2008 | from 2008 | Full | Worst drawdown | Change before 2008 | Change from 2008 |
|---|---|---|---|---|---|---|
| Naive: 12m moving average, equal notional, unlevered, fixed universe | 0.67 | 0.17 | 0.36 | -25.2% |  |  |
| + equal risk per market | 0.87 | 0.30 | 0.56 | -7.5% | +0.20 [-0.07, +0.47] | +0.12 [-0.04, +0.31] |
| + 1/3/12-month momentum signal | 1.01 | 0.30 | 0.62 | -6.2% | +0.14 [-0.15, +0.46] | +0.00 [-0.24, +0.22] |
| + cluster risk budget | 1.13 | 0.40 | 0.75 | -3.7% | +0.13 [-0.11, +0.34] | +0.10 [-0.06, +0.27] |
| + 15% ex-ante volatility target | 1.13 | 0.53 | 0.81 | -27.3% | -0.00 [-0.10, +0.09] | +0.14 [+0.02, +0.26] |
| + 25% carry | 1.20 | 0.57 | 0.87 | -25.6% | +0.07 [+0.00, +0.14] | +0.03 [-0.02, +0.09] |
| + yearly point-in-time universe | 1.28 | 0.58 | 0.91 | -27.4% | +0.08 [-0.09, +0.24] | +0.01 [-0.08, +0.11] |
| + volatility floor = the index | 1.27 | 0.53 | 0.87 | -27.0% | -0.02 [-0.03, -0.00] | -0.05 [-0.12, +0.01] |
| *Alternatives (change vs the index):* | | | | | | |
| sector budget instead of clusters | 1.25 | 0.35 | 0.77 | -40.6% | -0.02 [-0.16, +0.12] | -0.18 [-0.29, -0.07] |
| continuous trend strength instead of sign | 1.18 | 0.63 | 0.89 | -34.7% | -0.09 [-0.23, +0.06] | +0.10 [-0.04, +0.24] |
| 12-month momentum only | 0.95 | 0.43 | 0.67 | -33.1% | -0.31 [-0.73, +0.10] | -0.10 [-0.39, +0.20] |
| no carry | 1.19 | 0.52 | 0.83 | -27.9% | -0.08 [-0.17, +0.01] | -0.01 [-0.06, +0.03] |
| 50% carry | 1.34 | 0.53 | 0.91 | -29.7% | +0.07 [-0.06, +0.22] | -0.00 [-0.09, +0.08] |
| *Reference:* | | | | | | |
| AQR TSMOM ex-equity (gross of costs) | 1.37 | 0.42 | 0.81 | -33.4% |  |  |

Two choices were kept against the numbers, by judgment: the long/short sign signal rather than continuous trend strength (strength did better after 2008 and trades half as much, but worse before), and carry capped at 25% so the index stays a trend strategy. The volatility floor costs a little Sharpe; it is kept as protection against volatility regimes that end abruptly (section 7).

## 3. Do markets that trend badly keep trending badly?

Every candidate market's trend Sharpe ratio before 2006 against its trend Sharpe ratio after (38 markets, equal risk, gross):

- Rank correlation between the two halves: **0.01** (one-sided permutation p-value 0.46).
- Dropping the worst quarter on first-half evidence (British pound, Cocoa, Copper, Gilt 10Y, Gold, Platinum, Silver, Soybean meal, Soybean oil, Soybeans) would have moved the second-half Sharpe from 0.37 to 0.39.

There is no evidence that a market's past trend performance predicts its future trend performance. The test is not powerful (a few dozen noisy Sharpe ratios), so it cannot rule out a small effect, but it gives no reason to prune markets by their backtest; the gain from doing so here is within noise.

| Market | Sector | Before | After |
|---|---|---|---|
| Silver | commodity | -0.08 | -0.01 |
| Platinum | commodity | -0.04 | -0.03 |
| Soybean oil | commodity | -0.00 | 0.32 |
| British pound | fx | 0.13 | 0.07 |
| Soybean meal | commodity | 0.16 | -0.09 |
| Copper | commodity | 0.16 | 0.23 |
| Gold | commodity | 0.17 | 0.32 |
| Soybeans | commodity | 0.21 | -0.09 |
| Cocoa | commodity | 0.25 | -0.11 |
| Gilt 10Y | bond | 0.27 | 0.10 |
| Natural gas | commodity | 0.32 | 0.37 |
| Lean hogs | commodity | 0.32 | 0.02 |
| Corn | commodity | 0.33 | 0.20 |
| Cotton | commodity | 0.33 | 0.37 |
| Wheat (HRW) | commodity | 0.35 | 0.07 |
| JGB 10Y | bond | 0.38 | 0.57 |
| RBOB gasoline | commodity | 0.41 | 0.26 |
| Live cattle | commodity | 0.41 | 0.32 |
| Wheat (SRW) | commodity | 0.42 | -0.00 |
| US 30Y | bond | 0.43 | 0.07 |
| Heating oil | commodity | 0.44 | 0.23 |
| Norwegian krone | fx | 0.45 | -0.19 |
| US 10Y | bond | 0.46 | 0.25 |
| Sugar | commodity | 0.46 | 0.43 |
| Brent crude | commodity | 0.48 | 0.40 |
| WTI crude | commodity | 0.51 | 0.27 |
| Canadian dollar | fx | 0.53 | -0.16 |
| Australian dollar | fx | 0.53 | -0.11 |
| Swiss franc | fx | 0.55 | -0.20 |
| Japanese yen | fx | 0.55 | 0.41 |
| Euro | fx | 0.56 | 0.03 |
| US 5Y | bond | 0.57 | 0.29 |
| Bobl 5Y | bond | 0.61 | 0.11 |
| Bund 10Y | bond | 0.62 | 0.16 |
| Swedish krona | fx | 0.63 | -0.02 |
| Schatz 2Y | bond | 0.68 | 0.32 |
| US 2Y | bond | 0.76 | 0.24 |
| New Zealand dollar | fx | 0.90 | -0.21 |

## 4. Why trend following weakened after 2008

Scale every market's trend P&L to the same volatility and hold them in equal risk. The portfolio Sharpe ratio is then approximately

    SR = S_bar x sqrt(N / (1 + (N - 1) rho_bar))

with S_bar the average single-market Sharpe ratio and rho_bar the average correlation between the markets' trend P&Ls (exact for equal-volatility streams in equal weights). The square-rooted factor is the diversification multiplier; its square is the effective number of independent bets. All 40 candidates, trend signal only, gross.

| | Before 2008 | From 2008 |
|---|---|---|
| Average single-market Sharpe (S_bar) | 0.39 | 0.10 |
| Average correlation of trend P&Ls (rho_bar) | 0.06 | 0.13 |
| Effective independent bets | 11.3 | 6.4 |
| Predicted portfolio Sharpe | 1.32 | 0.26 |
| Realised portfolio Sharpe | 1.34 | 0.27 |
| Average correlation of the markets | 0.13 | 0.16 |
| Average position alignment | 0.07 | 0.08 |

The realised Sharpe ratio fell by 1.06: about 0.86 from weaker trends in the average market and 0.20 from lost diversification (effective bets 11.3 -> 6.4). Most of the extra correlation comes from positions being aligned while the markets co-move; the rest from positions aligning precisely when markets move together (shared macro trends):

| Trend co-movement | Before | From |
|---|---|---|
| total | 0.048 | 0.092 |
| alignment x market co-movement | 0.037 | 0.062 |
| shared trends (remainder) | 0.011 | 0.030 |

Average single-market trend Sharpe by sector:

| Sector | Before | From |
|---|---|---|
| commodity | 0.32 | 0.11 |
| bond | 0.46 | 0.21 |
| fx | 0.49 | -0.05 |

How many markets are enough? Equal-risk portfolios of N randomly drawn candidates (200 draws each, full sample, gross):

| N | Median Sharpe | 10th-90th percentile | Identity |
|---|---|---|---|
| 1 | 0.24 | 0.04 to 0.45 | 0.24 |
| 2 | 0.35 | 0.15 to 0.50 | 0.32 |
| 3 | 0.37 | 0.20 to 0.52 | 0.38 |
| 5 | 0.48 | 0.31 to 0.61 | 0.45 |
| 8 | 0.53 | 0.42 to 0.67 | 0.52 |
| 12 | 0.62 | 0.50 to 0.71 | 0.57 |
| 16 | 0.65 | 0.56 to 0.74 | 0.60 |
| 20 | 0.67 | 0.60 to 0.75 | 0.63 |
| 25 | 0.70 | 0.64 to 0.77 | 0.65 |
| 30 | 0.72 | 0.66 to 0.77 | 0.66 |
| 35 | 0.74 | 0.71 to 0.77 | 0.67 |
| 40 | 0.74 | 0.74 to 0.74 | 0.68 |

40 markets are worth about 8 independent bets; beyond about 20 markets, extra names mostly narrow the spread of outcomes.

## 5. Stacking the index on equities

100% S&P 500 total return (fully funded) plus the index as a futures overlay scaled to each volatility, 1989-02 to 2026-08, after costs and 20 bps a year of financing drag, before fees:

| Overlay volatility | Sharpe | Volatility | Worst drawdown | Annual return |
|---|---|---|---|---|
| 0% | 0.61 | 14.7% | -50.9% | 11.3% |
| 5% | 0.92 | 14.5% | -44.6% | 16.2% |
| 10% | 1.10 | 16.0% | -40.0% | 21.1% |
| 15% | 1.18 | 18.7% | -37.0% | 25.9% |
| 20% | 1.19 | 22.3% | -34.0% | 30.5% |
| 25% | 1.17 | 26.3% | -31.7% | 34.9% |

In the 44 months the S&P 500 fell more than 5% (average -7.5%), the index returned 3.31% on average. The stacked portfolio carries about the index's gross futures exposure on top of fully invested equities, so it needs the margin and cash buffer in section 7.

## 6. Weekly or monthly? And what if trades execute a day late?

Only the commodities have daily data, so this uses the 19 commodity futures, 1991 to March 2024, the index's trend signal, equal risk per market and the same trading costs:

| Rebalancing | Sharpe | Annual return | Volatility | Worst drawdown | Turnover a year | Cost a year | 1-year vol, 10th-90th pct |
|---|---|---|---|---|---|---|---|
| Monthly, at the signal close | 0.45 | 7.6% | 21.6% | -59.8% | 18x | 0.65% | 11.3%-34.8% |
| Monthly, one day later | 0.36 | 5.6% | 21.8% | -74.3% | 18x | 0.65% | 11.5%-34.6% |
| Weekly, at the signal close | 0.49 | 8.5% | 20.8% | -46.8% | 37x | 1.26% | 10.3%-31.1% |
| Weekly, one day later | 0.44 | 7.4% | 21.3% | -50.0% | 37x | 1.26% | 9.5%-30.1% |

Weekly rebalancing mainly improves risk: a shallower worst drawdown and steadier volatility, for twice the turnover. Executing a day after the signal costs a little at either frequency. The index is monthly because its bond and currency data are monthly; a live implementation would reasonably rebalance weekly. One sleeve and one history: treat the size of the gain as uncertain.

## 7. Practitioner views

**After fees.** The index is reported before any management fee; as a fund:

| Fees | Annual return | Sharpe | Worst drawdown |
|---|---|---|---|
| No fees | 16.2% | 0.89 | -22.7% |
| 1% management, 10% performance | 13.6% | 0.74 | -27.6% |
| 2% management, 20% performance | 11.2% | 0.60 | -32.0% |

**Leverage and margin.** Positions target 15% ex-ante volatility with gross notional capped at 8x. Estimated exchange margin (bonds 2%, currencies 4%, commodities 8% of notional) uses a median 16% of capital, 24% at the 95th percentile, 32% at most.

| Decade | Realised volatility | Sharpe | Median gross exposure | Months at the cap |
|---|---|---|---|---|
| 1980s | 11.4% | 1.20 | 3.6x | 0% |
| 1990s | 15.1% | 1.36 | 4.2x | 1% |
| 2000s | 16.0% | 0.98 | 4.3x | 1% |
| 2010s | 13.6% | 0.57 | 4.8x | 3% |
| 2020s | 15.3% | 0.47 | 4.7x | 6% |

**Concentration.** Share of the index's gross profit by market (top ten):

| Market | Share |
|---|---|
| Japanese yen | 17% |
| JGB 10Y | 15% |
| Euro | 13% |
| US 10Y | 10% |
| Gilt 10Y | 5% |
| WTI crude | 4% |
| Natural gas | 4% |
| Sugar | 4% |
| New Zealand dollar | 4% |
| Bund 10Y | 3% |

Japan (yen and JGB) contributed 32% of the profit. Without those two markets the Sharpe ratio is 0.71 (1.20 before 2008, 0.25 after): the yen's long trends and decades of falling Japanese yields were a large, possibly unrepeatable, source of profit. Low measured volatility can also end abruptly (the Swiss franc floor in 2015, the end of yield-curve control), which is why the index floors each market's risk estimate at half its sector's median.

**The approximated period.** Commodity data after 2024-03 comes from Yahoo front-month prices mapped onto the futures series. Sharpe to 2024-03: 0.94; since then (29 months): 0.05.

## 8. How close are the constructed series to real futures?

Bonds are built from yields and currencies from spot rates plus the interest-rate differential. Correlation of their monthly returns with the front-month futures (Yahoo, month-end, 2001 on):

| Market | Future | Correlation | Months |
|---|---|---|---|
| US 10Y | ZN=F | 0.95 | 308 |
| US 5Y | ZF=F | 0.95 | 308 |
| US 2Y | ZT=F | 0.93 | 308 |
| US 30Y | ZB=F | 0.93 | 308 |
| Euro | 6E=F | 0.98 | 308 |
| Japanese yen | 6J=F | 0.98 | 307 |
| British pound | 6B=F | 0.97 | 306 |
| Australian dollar | 6A=F | 0.98 | 305 |
| Canadian dollar | 6C=F | 0.97 | 308 |
| Swiss franc | 6S=F | 0.97 | 306 |

Commodities after March 2024 (regression of futures returns on Yahoo front-month returns; tracking error measured on a holdout after 2019):

| Commodity | Correlation | Slope | Tracking error (fit) | Tracking error (holdout) | Bias (holdout) |
|---|---|---|---|---|---|
| Brent crude | 0.96 | 0.97 | 10.1% | 12.5% | 8.5% |
| Live cattle | 0.74 | 0.55 | 8.7% | 8.9% | -6.3% | not extended |
| Cocoa | 0.97 | 0.91 | 7.7% | 11.5% | 3.2% |
| Coffee | 0.99 | 0.93 | 4.8% | 6.5% | 4.2% |
| Copper | 0.99 | 0.99 | 3.9% | 3.7% | -2.6% |
| Corn | 0.89 | 0.74 | 11.5% | 14.4% | 5.9% |
| Cotton | 0.90 | 0.81 | 11.9% | 11.9% | 5.7% |
| RBOB gasoline | 0.88 | 0.70 | 16.9% | 15.0% | 10.9% |
| Gold | 0.99 | 1.00 | 2.2% | 3.8% | -1.0% |
| Heating oil | 0.91 | 0.79 | 12.6% | 17.0% | 10.1% |
| Lean hogs | 0.57 | 0.32 | 18.2% | 23.7% | -11.4% | not extended |
| Wheat (HRW) | 0.96 | 0.92 | 8.4% | 8.5% | 6.6% |
| Natural gas | 0.93 | 0.82 | 16.3% | 18.9% | 1.6% |
| Platinum | 0.88 | 0.72 | 10.5% | 9.2% | 5.5% |
| Silver | 0.97 | 0.97 | 7.6% | 7.3% | 0.7% |
| Soybeans | 0.91 | 0.79 | 9.3% | 9.0% | -2.2% |
| Soybean meal | 0.91 | 0.77 | 10.6% | 9.9% | -3.1% |
| Soybean oil | 0.95 | 0.87 | 8.3% | 13.9% | 8.6% |
| Sugar | 0.93 | 0.83 | 10.5% | 8.8% | 6.2% |
| Wheat (SRW) | 0.93 | 0.81 | 10.1% | 8.2% | 2.3% |
| WTI crude | 0.89 | 0.61 | 12.4% | 23.7% | -0.3% |

## 9. The universe over time

Re-selected every January from point-in-time data (liquidity at least 15k contracts a day, return correlation below 0.70 with every more liquid member, incumbents kept unless the correlation exceeds it by 0.10). Liquidity ranks use today's volumes, as historical volumes are not freely available.

| Review | Markets | Joined | Left |
|---|---|---|---|
| 1989 | 19 | Australian dollar, Canadian dollar, Cocoa, Corn, Cotton, Euro, Gilt 10Y, Gold, Heating oil, Lean hogs, Live cattle, New Zealand dollar, Platinum, Silver, Soybean meal, Soybean oil, Sugar, US 10Y, Wheat (SRW) | - |
| 1990 | 20 | JGB 10Y | - |
| 1993 | 20 | Brent crude | Heating oil |
| 1994 | 22 | Japanese yen, Natural gas, WTI crude | Brent crude |
| 1999 | 23 | Copper | - |
| 2001 | 24 | Bund 10Y | - |
| 2011 | 25 | Coffee | - |
| 2013 | 26 | Schatz 2Y | - |
| 2020 | 25 | - | Gilt 10Y |
| 2025 | 23 | - | Lean hogs, Live cattle |
