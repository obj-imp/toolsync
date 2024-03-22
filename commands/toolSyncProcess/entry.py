import adsk, traceback
import os
from ...lib import fusion360utils as futil
from ... import config

from urllib.parse import urlencode
from .packages import urllib3
import json
import time
import copy

import threading

from . import configuration

app = adsk.core.Application.get()
ui = app.userInterface


http = urllib3.PoolManager()

# check configuration
try:
    API_KEY = configuration.API_KEY
    BASE_ID = configuration.BASE_ID
    TABLE_NAME = configuration.TABLE_NAME
    syncInterval = configuration.syncInterval
    targetLibName = configuration.targetLibName
    targetLibLocation = configuration.targetLibLocation
    maxToolsToRead = configuration.maxToolsToRead
except:
    futil.log(f"Failed to load configuration from configuration.py")
    exit(-1)

ENDPOINT = f'https://api.airtable.com/v0/{BASE_ID}/{TABLE_NAME}'
HEADERS = {
    'Authorization': f'Bearer {API_KEY}',
    'Content-Type': 'application/json'
}

lastUpdateToolLibrary = {}
onStartup = True


# TODO *** Specify the command identity information. ***
CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_cmdDialog'
CMD_NAME = 'ToolSync'
CMD_Description = 'Synchronize S700 tool library with AirTable'

# Specify that the command will be promoted to the panel.
IS_PROMOTED = True

# TODO *** Define the location where the command button will be created. ***
# This is done by specifying the workspace, the tab, and the panel, and the 
# command it will be inserted beside. Not providing the command to position it
# will insert it at the end.
WORKSPACE_ID = 'CAMEnvironment'
PANEL_ID = 'CAMScriptsAddinsPanel'
COMMAND_BESIDE_ID = 'ScriptsManagerCommand'

# Resource location for command icons, here we assume a sub folder in this directory named "resources".
ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', '')

# Local list of event handlers used to maintain a reference so
# they are not released and garbage collected.
local_handlers = []
stopFlag = None

# Executed when add-in is run.
def start():
    # Create a command Definition.
    cmd_def = ui.commandDefinitions.addButtonDefinition(CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER)

    # Define an event handler for the command created event. It will be called when the button is clicked.
    futil.add_handler(cmd_def.commandCreated, command_created)

    # ******** Add a button into the UI so the user can run the command. ********
    # Get the target workspace the button will be created in.
    workspace = ui.workspaces.itemById(WORKSPACE_ID)

    # Get the panel the button will be created in.
    panel = workspace.toolbarPanels.itemById(PANEL_ID)

    # Create the button command control in the UI after the specified existing command.
    control = panel.controls.addCommand(cmd_def, COMMAND_BESIDE_ID, False)

    # Specify if the command is promoted to the main toolbar. 
    control.isPromoted = IS_PROMOTED

    futil.log(f'ToolSync : Forcing syncronization of full tool library on startup!')
    sync_all_tools()

    # Create a new thread for the other processing. 
    if syncInterval > 0:
        futil.log(f'ToolSync : scheduling background synchronization every {syncInterval} seconds')       
        global stopFlag        
        stopFlag = threading.Event()
        syncThread = backgroundSyncThread(stopFlag)
        syncThread.start()
    else:
        futil.log(f'ToolSync : background synchronization disabled')


# Executed when add-in is stopped.
def stop():
    # Get the various UI elements for this command
    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    panel = workspace.toolbarPanels.itemById(PANEL_ID)
    command_control = panel.controls.itemById(CMD_ID)
    command_definition = ui.commandDefinitions.itemById(CMD_ID)

    # Delete the button command control
    if command_control:
        command_control.deleteMe()

    # Delete the command definition
    if command_definition:
        command_definition.deleteMe()

    # stop the thread
    if syncInterval > 0:
        stopFlag.set() 


class backgroundSyncThread(threading.Thread):
    def __init__(self, event):
        threading.Thread.__init__(self)
        self.stopped = event

    def run(self):
        # Every five seconds fire a custom event, passing a random number.
        while not self.stopped.wait(syncInterval):
            futil.log(f'ToolSync : periodic library synchronization')
            sync_all_tools()
   

# Function that is called when a user clicks the corresponding button in the UI.
# This defines the contents of the command dialog and connects to the command related events.
def command_created(args: adsk.core.CommandCreatedEventArgs):

    # https://help.autodesk.com/view/fusion360/ENU/?contextId=CommandInputs
    inputs = args.command.commandInputs

    futil.log(f'ToolSync : Scanning tool library for changes and synchronizing with AirTable...')
    sync_all_tools()



def initialize_tool_library():
    lastUpdateToolLibrary = copy.deepcopy(read_current_tool_library())

def read_current_tool_library():

    try:

        newToolLib = {}
        futil.log(f'Reading current tool library {targetLibName}')

        # Get the active document.
        active_document = app.activeDocument

        # Get a reference to the CAMManager object.
        camMgr = adsk.cam.CAMManager.get()

        # Get the ToolLibraries object.
        toolLibs = camMgr.libraryManager.toolLibraries

        # Get the URL for the local libraries.
        if targetLibLocation == 'LOCAL':
            LibLocationURL = toolLibs.urlByLocation(adsk.cam.LibraryLocations.LocalLibraryLocation)
        else:
            LibLocationURL = toolLibs.urlByLocation(adsk.cam.LibraryLocations.CloudLibraryLocation)

        # Get the named library.
        f360LibraryURLs = toolLibs.childAssetURLs(LibLocationURL)
        toolLib = None
        for libURL in f360LibraryURLs:
            if targetLibName in libURL.leafName:
                toolLib = toolLibs.toolLibraryAtURL(libURL)
                break
        
        if not toolLib: 
            futil.log(f'{targetLibName} tool library not found!')
            raise Exception('Tool library not found')
        
        toolCount = 0
        for tool in toolLib:
            toolJsonString = tool.toJson()
            toolJson = json.loads(toolJsonString)
            newToolLib[toolJson['description']] = generate_airtable_entry(tool)
            toolCount += 1
            
            # DEBUG : limit number of tools read
            if toolCount = maxToolsToRead:
                break

            adsk.doEvents()

        futil.log(f'DONE! (read {toolCount} tools out of {toolLib.count} in {targetLibName} library)')

    except:
        if ui:
            ui.messageBox('Failed to read tool library:\n{}'.format(traceback.format_exc()))

    return newToolLib


def sync_all_tools():

    futil.log(f'Synchronizing all tools in library to airtable...')
    newToolLib = read_current_tool_library()
    toolProcessed = 0
    toolsCreated = 0
    toolsUpdated = 0
    for k in newToolLib.keys():
        toolProcessed += 1
        if k not in lastUpdateToolLibrary.keys():
            sync_individual_tool(newToolLib[k])
            lastUpdateToolLibrary[k] = newToolLib[k]
            toolsCreated += 1
            adsk.doEvents()
        else:
            if newToolLib[k] != lastUpdateToolLibrary[k]:
                sync_individual_tool(newToolLib[k])
                lastUpdateToolLibrary[k] = newToolLib[k]
                toolsUpdated += 1
                adsk.doEvents()

    futil.log(f'DONE! (processed {toolProcessed} tools, forced sync {toolsCreated} potential new tools, updated {toolsUpdated} entries in AirTable)')
    

def force_sync_all_tools():
    futil.log(f'force_sync_all_tools() called')
    futil.log(f'clearing tool library')
    lastUpdateToolLibrary = {}

    sync_all_tools()


tc = { 'Unit (tool_unit)': 'unit',
        'Type (tool_type)' : 'type'}

string_expression_map = { 
        'tool_comment' : 'Comment (tool_comment)' ,
        'tool_productId' : 'Product ID (tool_productId)',
        'tool_vendor' : 'Vendor (tool_vendor)',
        'tool_productLink' : 'Product Link (tool_productLink)',
        'tool_unit' : 'Unit (tool_unit)'
        }

number_expression_map = { 
        'tool_diameter' : 'Diameter (tool_diameter)',
        'tool_bodyLength' : 'Body Length (tool_bodyLength)',
        'tool_cornerRadius' : 'Corner Radius (tool_cornerRadius)',
        'tool_fluteLength' : 'Flute Length (tool_fluteLength)',
        'tool_numberOfFlutes' : 'Number of Flutes (tool_numberOfFlutes)',
        'tool_overallLength' : 'Overall Length (tool_overallLength)',
        }

def generate_airtable_entry(tool):

    toolJsonString = tool.toJson()
    toolJson = json.loads(toolJsonString)

    try:

        fields_to_update = { 'Description (tool_description)': toolJson['description'] }

        try:
            fields_to_update['Comment (tool_comment)'] = toolJson['post-process']['comment']
        except:
            pass


        try:
            fields_to_update['Holder Description (holder_description)'] = toolJson['holder']['description']
        except:
            pass

        try:
            fields_to_update['Gauge Length (tool_assemblyGaugeLength)'] = round(toolJson['geometry']['assemblyGaugeLength'], 5)
        except:
            pass
            
        try:
            fields_to_update['Flute Length (tool_fluteLength)'] = round(toolJson['geometry']['LCF'], 6)
        except:
            pass

        try:
            fields_to_update['Number of Flutes (tool_numberOfFlutes)'] = round(toolJson['geometry']['NOF'], 6)
        except:
            pass

        try:
            fields_to_update['Overall Length (tool_overallLength)'] = round(toolJson['geometry']['OAL'], 6)
        except:
            pass

        try:
            fields_to_update['Diameter (tool_diameter)'] = round(toolJson['geometry']['SFDM'], 6)
        except:
            pass

        try:
            fields_to_update['Corner Radius (tool_cornerRadius)'] = round(toolJson['geometry']['RE'], 6)
        except:
            pass

        try:
            fields_to_update['Body Length (tool_bodyLength)'] = round(toolJson['geometry']['LB'], 6)
        except:
            pass
        
        for k in toolJson.keys():
            if k in tc.keys():
                continue
            if k == 'expressions':
                continue

        for k in tc.keys():
            try:
                fields_to_update[k] = toolJson[tc[k]]
            except:
                pass
        
        try:
            for k in toolJson['expressions']:

                try:
                    if k in string_expression_map.keys():
                        fields_to_update[string_expression_map[k]] = strip_quotes(toolJson['expressions'][k])
                    # NOTE : use numbers from geometry instead to avoid mixed unit hell from operators
                    #elif k in number_expression_map.keys():
                    #    fields_to_update[number_expression_map[k]] = strip_quotes_etc(toolJson['expressions'][k])
                    #    importedFields.append('expressions.'+k)
                    else:
                        pass
                except KeyError as e:
                    futil.log(f'KeyError : {e} searching for {k} in {toolJson["expressions"]}')
                    continue

        except:
            pass
    except KeyError as e:
        futil.log(f'KeyError : {e} in main loop')
        futil.log(f' processing tool : {toolJsonString}')
        return None
    
    return fields_to_update


def sync_individual_tool(toolUpdates):

    if toolUpdates is None:
        futil.log(f'failed to generate airtable entry for tool : {tool.description}')
        futil.log(f'tool.json : {tool.toJson()}')
        return False
    
    futil.log(f'updating tool : {toolUpdates["Description (tool_description)"]}')

    if not upsert_tool(toolUpdates["Description (tool_description)"], toolUpdates):
        futil.log(f'failed to update tool : {toolUpdates["Description (tool_description)"]}\n')
        return False

    return True


def find_records_by_field(field_name, field_value):
    """
    Find records in an Airtable table where the given field matches the specified value.
    
    :param field_name: The name of the field to search.
    :param field_value: The value to match in the specified field.
    :return: A list of matching records, or None if an error occurred.
    """
    # Encode the filter formula
    formula = urlencode({'filterByFormula': f"{{{field_name}}} = '{field_value}'"})
    url = f"{ENDPOINT}?{formula}"

    # Make the GET request
    response = http.request('GET', url, headers=HEADERS)
    
    if response.status == 200:
        # Parse the response body
        data = json.loads(response.data.decode('utf-8'))
        return data.get('records', [])
    else:
        print(f"Failed to fetch records. Status code: {response.status}")
        return None


def upsert_tool(search_value, fields_to_update):
    
    filter = find_records_by_field('Description (tool_description)', search_value)

    if filter:

        if len(filter) > 1:
            futil.log("WARNING : Multiple matching records found it tool library!")
        for r in filter:
            record = r
            break
        
        # Record exists, update it (TODO: check if record actually needs to be updated would be a lot faster)
        record_id = record['id']
        update_response = http.request('PATCH', f"{ENDPOINT}/{record_id}", headers=HEADERS, body=json.dumps({"fields": fields_to_update}))
        if update_response.status == 200:
            pass
        else:
            futil.log(f"WARNING : Failed to update record {search_value} in airtable with error code {update_response.status} and response {update_response.data}")
            futil.log(f'fields_to_update : {fields_to_update}')
            return False
    else:
        # Record does not exist, create it
        create_response = http.request('POST', ENDPOINT, headers=HEADERS, body=json.dumps({"records": [{"fields": fields_to_update}]}))
        if create_response.status == 200:
            pass
        else:
            futil.log(f"WARNING : Failed to create record {search_value} in airtable with error code {create_response.status}and response {create_response.data}")
            futil.log(f'fields_to_update : {fields_to_update}')
            return False 
        
    return True

def strip_quotes(s):
    return s.replace('"', '').replace("'", '')

def strip_quotes_etc(s):
    if s[-2:] == 'in':
        s = s[:-2]
    if s[-2:] == 'mm':
        s = s[:-2]
    s = s.replace('"', '').replace("'", '').replace(' ', '')
    
    try:
       return eval(s)
    except:
        return s


