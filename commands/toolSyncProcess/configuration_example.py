
# this is an example configuration file for the ToolSync add-in
#
# fill in the applicable values and save this file as 'configuration.py' in the same directory as the ToolSync add-in
#

API_KEY = "xxxxxx"                   # your airtable personal access token
BASE_ID = "xxxx"                     # airtable base id (from the url)
TABLE_NAME = "xxxx"                  # airtable table name (from the url)

targetLibName = 'S700 16K_121223'    # name of tool library to sync
targetLibLocation = 'CLOUD'          # set to 'CLOUD' or 'LOCAL' accordingly
syncInterval = 15                    # how often to sync tools to airtable in background (0 to disable background sync)