"""Managed futures trend-following index -- a from-scratch, dependency-free build.

Modules:
    store      SQLite schema and read/write helpers
    fetch      HTTP helper, Yahoo daily prices, month-end aggregation
    sources    public data sources (World Bank, BIS, Fed H.15, Bundesbank, BoE, AQR)
    model      trend signal, position sizing, and the backtest loop
    metrics    performance statistics
    panel      universe construction and standard variant definitions
    report     static HTML charts and tables
    report_page  full HTML report builder

Deliberately standard-library only.
"""
