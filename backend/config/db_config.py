# #db_config.py


import pyodbc
from config.settings import SERVER_NAME

def get_connection(database):
    try:
        conn_str = (
            "DRIVER={ODBC Driver 18 for SQL Server};"
            f"SERVER={SERVER_NAME};"
            f"DATABASE={database};"
            "Trusted_Connection=yes;"
            "TrustServerCertificate=yes;"
        )

        return pyodbc.connect(conn_str, timeout=10)

    except Exception as e:
        print(f"Error connecting to database: {e}")
        return None


# import pyodbc

# from config.settings import SERVER_NAME

# def get_connection(database):
#     conn = None
#     try:
#         conn = pyodbc.connect(
#             f"""
#             DRIVER={{ODBC Driver 18 for SQL Server}};
#             SERVER={SERVER_NAME};
#             DATABASE={database};
#             Trusted_Connection=yes;
#             """
#         )
#         return conn
#     except Exception as e:
#         print(f"Error connecting to database: {e}")
#         return None


# def get_connection(database_name):
#     try:
#         conn = pyodbc.connect(
#             f"""
#             DRIVER={{ODBC Driver 17 for SQL Server}};
#             SERVER={SERVER_NAME};
#             DATABASE={database_name};
#             UID={{edsadmin}};
#             PWD={{Welcome@2026}};
#             """
#         )
#         return conn
#     except Exception as e:
#         print(e)
#         return None