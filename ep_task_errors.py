#!/usr/bin/env python3
"""
Monitor Nearline endpoint

Based on tutorial and documentation at:
   http://globus.github.io/globus-sdk-python/index.html
-Galen Arnold, 2018, NCSA
"""
import time
import re
import os
import json
import webbrowser
import pprint
import globus_sdk
import sys

# some globals
CLIENT_ID = '231634e4-37cc-4a06-96ce-12a262a62da7'
DEBUG = 0
TIMEOUT = 60
MB = 1048576
NOTIFY_SIZE = 100
RECIPIENTS = "gwarnold@illinois.edu,gbauer@illinois.edu"
RECIPIENTS = "gwarnold@illinois.edu,help+bwstorage@ncsa.illinois.edu"
GLOBUS_CONSOLE = "https://www.globus.org/app/console/tasks/"
DISPLAY_ONLY_SIZE = NOTIFY_SIZE
PAUSE_SIZE = NOTIFY_SIZE
SRCDEST_FILES = 500
SLEEP_DELAY = 300
# dictionaries for testing to maintain state of tasks and users notified
MYTASK_NOTED = {}
MYUSER_NOTIFIED = {}
TOKEN_FILE = 'refresh-tokens.json'
REDIRECT_URI = 'https://auth.globus.org/v2/web/auth-code'
SCOPES = ('openid email profile '
          'urn:globus:auth:scope:transfer.api.globus.org:all')
# endpoints determined by globus cli: globus endpoint search ncsa#jyc
#  or from globus.org -> "Manage Endpoints" -> endpoint detail, UUID
EP_BW = "d59900ef-6d04-11e5-ba46-22000b92c6ec"
EP_JYC = "d0ccdc02-6d04-11e5-ba46-22000b92c6ec"
#EP_NEARLINE = "d599008e-6d04-11e5-ba46-22000b92c6ec"

GET_INPUT = getattr(__builtins__, 'raw_input', input)
IS_STDOUT_FILE_MISSING = re.compile(r"File: .*\.OU")
IS_STDERR_FILE_MISSING = re.compile(r"File: .*\.ER")


def is_remote_session():
    """ Test if this is a remote ssh session """
    return os.environ.get('SSH_TTY', os.environ.get('SSH_CONNECTION'))


def load_tokens_from_file(filepath):
    """Load a set of saved tokens."""
    tokens = {}
    try:
        with open(filepath, 'r') as tokenfile:
            tokens = json.load(tokenfile)
    except FileNotFoundError:
        pass
    except BaseException:
        sys.stderr.write("Failed to read tokens from {}\n".format(filepath))
    return tokens


def save_tokens_to_file(filepath, tokens):
    """Save a set of tokens for later use."""
    with open(filepath, 'w') as tokenfile:
        json.dump(tokens, tokenfile)


def update_tokens_file_on_refresh(token_response):
    """
    Callback function passed into the RefreshTokenAuthorizer.
    Will be invoked any time a new access token is fetched.
    """
    save_tokens_to_file(TOKEN_FILE, token_response.by_resource_server)


def do_native_app_authentication(client_id, redirect_uri,
                                 requested_scopes=None):
    """
    Does a Native App authentication flow and returns a
    dict of tokens keyed by service name.
    """
    client = globus_sdk.NativeAppAuthClient(client_id=client_id)
    # pass refresh_tokens=True to request refresh tokens
    client.oauth2_start_flow(requested_scopes=requested_scopes,
                             redirect_uri=redirect_uri,
                             refresh_tokens=True)

    url = client.oauth2_get_authorize_url()

    print('Native App Authorization URL: \n{}'.format(url))

    if not is_remote_session():
        webbrowser.open(url, new=1)

    auth_code = GET_INPUT('Enter the auth code: ').strip()

    token_response = client.oauth2_exchange_code_for_tokens(auth_code)

    # return a set of tokens, organized by resource server name
    return token_response.by_resource_server


def my_endpoint_manager_task_list(tclient, endpoint):
    """
    Get tasks from an endpoint, then look through them for error events.
    Also mark as SRC, DEST, or SRC_DEST as the case may be.
    """
    source_total_files = 0
    dest_total_files = 0
    source_total_bps = 0
    dest_total_bps = 0
    source_total_tasks = 0
    dest_total_tasks = 0

    for task in tclient.endpoint_manager_task_list(filter_endpoint=endpoint,
                                                   filter_status="ACTIVE",
                                                   num_results=None):
        if task["destination_endpoint_id"] == endpoint:
            endpoint_is = "DEST"
            dest_total_files += task["files"]
            dest_total_bps += task["effective_bytes_per_second"]
            dest_total_tasks += 1
        else:
            endpoint_is = "SRC"
            source_total_files += task["files"]
            source_total_bps += task["effective_bytes_per_second"]
            source_total_tasks += 1
        if task["destination_endpoint_id"] == task["source_endpoint_id"]:
            endpoint_is = "DEST_SRC"
            dest_total_files += task["files"]
            dest_total_bps += task["effective_bytes_per_second"]
            dest_total_tasks += 1
            source_total_files += task["files"]
            source_total_bps += task["effective_bytes_per_second"]
            source_total_tasks += 1
        print("{1:10s} {2:36s} {3:10d} {0}".format(
            task["owner_string"], endpoint_is,
            task["task_id"],
            task["files"])
             )
        old_handled_count = 0
        # this logic will alert on the most recent error event for a task, only once
        for event in tclient.endpoint_manager_task_event_list(task["task_id"],
                                                              num_results=None,
                                                              filter_is_error=1):
            # for events that are transient, self-correct, or beyond user control,
            # skip over with continue
            if (event["code"] == "AUTH" or
                    event["code"] == "CANCELED" or
                    event["code"] == "CONNECT_FAILED" or
                    event["code"] == "CONNECTION_BROKEN" or
                    event["code"] == "CONNECTION_RESET" or
                    event["code"] == "ENDPOINT_TOO_BUSY" or
                    event["code"] == "ENDPOINT_ERROR" or
                    event["code"] == "FILE_SIZE_CHANGED" or
                    event["code"] == "GC_NOT_CONNECTED" or
                    event["code"] == "GC_PAUSED" or
                    event["code"] == "NO_APPEND_FILESYSTEM" or
                    event["code"] == "PERMISSION_DENIED" or
                    event["code"] == "PAUSED" or
                    event["code"] == "UNPAUSED" or
                    event["code"] == "TIMEOUT" or
                    event["code"] == "UNKNOWN" or
                    event["code"] == "VERIFY_CHECKSUM"):
                continue
                # skip over FILE_NOT_FOUND if it's just a missing batch spool file
            if event["code"] == "FILE_NOT_FOUND":
                stdout_file_name = IS_STDOUT_FILE_MISSING.search(event["details"])
                stderr_file_name = IS_STDERR_FILE_MISSING.search(event["details"])
                if not(stdout_file_name is None and stderr_file_name is None):
                    continue
            if MYTASK_NOTED.get(str(task["task_id"])) is None:
                # skip past user already notified
                if MYUSER_NOTIFIED.get(str(task["owner_string"] + event["code"])) == 1:
                    continue
                print("  {} {} {}".format(event["time"], event["code"],
                                          event["description"]))
                MYUSER_NOTIFIED[str(task["owner_string"] + event["code"])] = 1
                globus_url = GLOBUS_CONSOLE + str(task["task_id"])
                detail_file = open('task_detail.txt', 'w')
                detail_file.write("Click link to view in the GO console: {}\n".
                                  format(globus_url))
                detail_file.write("{} {} {}\n{}".format(event["time"],
                                                        event["code"], event["description"],
                                                        event["details"]))
                pprint.pprint(str(task), stream=detail_file, depth=1, width=50)
                detail_file.close()
                os.system("mail -s " + "ERROR:" + task["owner_string"] + " " + RECIPIENTS
                          + " < task_detail.txt")
            else:
                if old_handled_count < 1:
                    print("  old_or_handled: {} {} {}".format(event["time"], event["code"],
                                                              event["description"]))
                old_handled_count += 1
            MYTASK_NOTED[str(task["task_id"])] = 1
    # end for
    print("...TOTAL.files..tasks..MBps...")
    print("SRC  {:9d}  {:4d}  {:6.1f}".format(
        source_total_files, source_total_tasks, source_total_bps/MB)
         )
    print("DEST {:9d}  {:4d}  {:6.1f}".format(
        dest_total_files, dest_total_tasks, dest_total_bps/MB)
         )
    save_tokens_to_file("tasknoted.json", MYTASK_NOTED)
    save_tokens_to_file("usersnotified.json", MYUSER_NOTIFIED)


def main():
    """
    main program
    """
    tokens = None
    try:
        # if we already have tokens, load and use them
        tokens = load_tokens_from_file(TOKEN_FILE)
    except IOError:
        print("{} did not contain tokens [IOError].".format(TOKEN_FILE))

    if not tokens:
        # if we need to get tokens, start the Native App authentication process
        tokens = do_native_app_authentication(CLIENT_ID, REDIRECT_URI, SCOPES)

        try:
            save_tokens_to_file(TOKEN_FILE, tokens)
        except IOError:
            print("[IOError] unable to save tokens to {}".format(TOKEN_FILE))

    transfer_tokens = tokens['transfer.api.globus.org']

    auth_client = globus_sdk.NativeAppAuthClient(client_id=CLIENT_ID)

    authorizer = globus_sdk.RefreshTokenAuthorizer(
        transfer_tokens['refresh_token'],
        auth_client,
        access_token=transfer_tokens['access_token'],
        expires_at=transfer_tokens['expires_at_seconds'],
        on_refresh=update_tokens_file_on_refresh)

    tclient = globus_sdk.TransferClient(authorizer=authorizer)

    while True:
        print("...BW................task.[ACTIVE]............Nfiles.....owner...")
        my_endpoint_manager_task_list(tclient, EP_BW)
        print("...sleeping {}s...\n".format(SLEEP_DELAY))
        time.sleep(SLEEP_DELAY)
        # end while
# end def main()


MYTASK_NOTED = load_tokens_from_file("tasknoted.json")
MYUSER_NOTIFIED = load_tokens_from_file("usersnotified.json")
main()
