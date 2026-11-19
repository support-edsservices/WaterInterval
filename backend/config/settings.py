# config/settings.py

SERVER_NAME = r"TCLHSRPECDT0401\SQLEXPRESS"
TRUSTED_CONNECTION = False

INTERVAL_DB = 'TEAL_Water_AllData'            # database that holds the result tables (created if missing)
SOURCE_TABLE_DB = 'Teal_Water_Data'  # database that holds the raw readings

INTERVAL_STORAGE = 5   # MINUTES. 5 -> one row every 5 min, 10 -> one row every 10 min
RUN_INTERVAL_SEC = 5   # SECONDS. The program checks the source table and updates the result tables this often
WAIT_MINUTES = 7       # MINUTES. An interval row is written only this long after its interval time (should be INTERVAL_STORAGE + 2 or more:
                       # 5 min interval -> 7, 10 min interval -> 12)

# Source table
SOURCE_TABLE_ALL_DATA = 'TEAL_WATER_DATA'

# Forward tables (created if missing)
INTERVAL_TABLE = 'Daily_Water_Interval'
DAILY_TABLE = 'Daily_Water'

METER_IDS = [1, 2, 3, 4, 5, 6, 7, 8, 9]   # add 10, 11 ... here and the columns are created automatically

COLUMN_NAME_PREFIX = 'S1N'
SOURCE_COLUMN_SUFFIX = '_Water_Tot_Fwd_VAL0'   # source column : S1N{ID}_Water_Tot_Fwd_VAL0
COLUMN_NAME_SUFFIX = '_Water_FWD_VAL0'               # result column : S1N{ID}_FWD_VAL0

WATCH_BACK_DAYS = 2   # today + this many previous days are (re)calculated on every run