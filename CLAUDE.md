Working on a Fantasy Football Auction dashboard for the 26-27 season. 

Goals:

    The ultimate goal is to get used to using ClaudeCode and create a reasonably useful and easy tool for fantasy auctions.

    Engine to create projections. Can be in the form of:
        Player past stats -> projected points -> dollars worth
        ADP -> Dollars
        Should use Baysean Forecasting or Base Rates
            Don't want any player to be off the charts in value, rookies should be compared to past rookies, etc
    
    Dynamically reprice players based on auction results
        If a position is going for less thn projection -> average discount applied to all of that position
    
    Frontend:
        should be able to toggle between No, Half, or Full PPR
        should be able to select team composition (#QB, RB, Flex, etc)
        should be able to input budget
        Should be able to manually input live draft results
        Should show recommended players with name, past stats, projected points, ADP, dynamic dollar worth, etc

    Sharing:
        A non-technical person should be able to easily open the dashboard, hopefully with a link. The user should not need to download anything or run command prompts.

Tools:
    In the past I have used python, pandas, beautifulSoup, scikitlearn, streamlit, vercel, react, and typescript. I am open to recommendations.

    GitHub: https://github.com/BergerKing15 
        I have not created a repository yet. LMK if you need my password

Data:
    SUPERSEDED — FantasyData is paywalled. Live sources are now nfl_data_py (stats, players,
    schedules) and DraftSharks (auction values + ADP). See README.md "Data Sources".
    Original plan below, kept for reference:

    ADP data from https://fantasydata.com/nfl/adp?season=2025&team= (or https://fantasydata.com/nfl/ppr-adp?season=2025&team= for ppr). Ranges from 2014 to 2026

    Performance stats from https://fantasydata.com/nfl/fantasy-football-leaders?scope=season&sp=2025_REG&scoring=fpts_ppr&order_by=fpts_ppr&sort_dir=desc

    NFL Schedules from https://fantasydata.com/nfl/schedule?season=2026

    use draftsharks.com for better data. login is in .env as DRAFT_SHARKS_USER and DRAFT_SHARKS_PASSWORD . https://www.draftsharks.com/auction-values has their auction values to use as a reference. Obviously shouldn't be exactly the same as theirs but good sanity check. make sure to use it. Use https://www.draftsharks.com/rankings/ppr for ADP. Can also look at https://www.draftsharks.com/kb/best-auction-draft-strategy-salary-cap for strategy help if you want. 

File Tree:
    CLAUDE.md
        this
    NOTES.md
        Your place to take notes to recover context between sessions
    README.md
        Typical README including overview, methods, files, user guide, etc
    TODO.txt
        My reported issues / feature requests, with your resolution noted per item
    .env
        DraftSharks login (gitignored) — needed only to re-fetch data
    projection_engine.py
    backtest.py
        backtest the projection engine
    reprice_engine.py
    webscraping.py
    data
        whatever data from webscraping and outputted from the engines
        (tracked in git so the deployed dashboard has data)
    dashboard
        whatever for the frontend

Tasks:
    1. Gather Context
        Look through this file and the project directory. Do research on fantasy football auctions if nessesary. Look at the data sources provided
    
    2. Ask Questions, make recommendations, etc

    3. Write initial README
    
    4. Webscrape

    5. Build projection engine

    6. Backtest

    7. Build reprice engine

    8. Build dashboard

    9. Deploy

    Periodically:
        Push changes to github, take notes, edit readme as nessasary (after initially writing it), review code for bugs, inefficiencies, bad organization, etc
        
Other Notes:
    Make sure to use good ML principles and keep it simple.
        Seperate train and test, don't overfit
    
    This is my first time using claude code. do not take my word as the end all be all. If i can use you more effectively let me know. if you need more information or context let me know. if i need to download anything let me know. If the goals can be reached more effectively with different tools or frameworks let me know. 

    After changes commit to github within reason. use descriptive messages. 

    periodically scan the codebase for bugs, errors, or inefficiencies

    If you believe there is a better source for data, let me know

    keep code organized and readable. Comment for ease. Follow good programming principles.




