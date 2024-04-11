import os
import sys
import select
import socket
import queue
import time
import re


DEFAULTHOST = '' # We don't have a hostname! Use localhost aka 127.0.0.1
DEFAULTPORT = 80 # SET THIS TO 80 WHEN WE ARE READY
UNTIL_TIMEOUT = 29 # Time we take until we timeout a connection
SELECT_TIMEOUT = 10 # Time select() waits until it times out
SEND_BUFFER = 128 # Amount of bytes to send each response while transmitting a file
CWD = os.getcwd()


# A lookup for code meanings, will make doings logs and responses easier
RESPONSE_CODE_MEANINGS = {0: "HTTP/1.0 000 I Messed Up Real Bad Somewhere LMAO",
                200: "HTTP/1.1 200 OK",
                400: "HTTP/1.1 400 Bad Request",
                404: "HTTP/1.1 404 Not Found",}


# Holds an HTTP request, weither it be full or not
class HTTPRequest :
    def __init__(self, request) -> None:
        # Split request into parts over newline characters
        split_request = re.split(r"[\n\r]{1,2}", request)
        self.request_command = split_request[0]

        if len(split_request) >= 2:
            self.header_lines = split_request[1:]
        else: 
            self.header_lines = []

        if self.is_valid_command():
            self.file_to_return = CWD + self.request_command.split()[1].rstrip()
            if not self.file_exists():
                self.file_to_return = None
        else:
            self.file_to_return = None

        self.connection_type = "close"
    
    def apply_headers(self) -> None:
        # Goes through all headers and applies any valid ones to our request
        
        for h in self.header_lines:
            if re.match(r"^connection: ((keep-alive)|(close))\s*$", h.lower()) != None:
                self.connection_type = h.split()[1].rstrip() 

    def is_valid_command(self) -> bool:
        # Returns true if the given header for this program is valid, else false
        return re.match(r"GET \S+ HTTP/1\.1\s?", self.request_command) != None

    def is_valid_request(self) -> bool:
        return self.is_valid_command() # Turns out headers don't have to be valid
    
    def file_exists(self) -> bool:
        # Returns true if this response has a file that exists.

        if self.file_to_return == None:
            return False
        else:
            return os.path.isfile(self.file_to_return)


    def add_new_headers(self, new_lines) :
        split_headers = re.split(r"[\n\r]{1,2}", new_lines)

        for h in split_headers:
            if h.lstrip() != '':
             self.header_lines.append(h)
    
    def __str__(self) -> str:
        return (self.request_command + str(self.header_lines))


class HTTPResponse:
    
    def __init__(self, code = 0, request = HTTPRequest) -> None:
        # Holds an HTTP response
        self.code = code
        self.request_command = request.request_command
        self.connection_type = request.connection_type
        self.response_file = request.file_to_return
        self.response_time = self.get_current_datetime()
        self.file = None
        self.bytes_read = 0
        self.file_size = 0
    
    def get_current_datetime(self) -> str:
        # Returns a string with the info needed for the print logs about the current time
        return time.strftime("%a %b %d %H:%M:%S %Z %Y", time.localtime())

    def get_response_message(self) -> str:
        # Gets a string code
        return RESPONSE_CODE_MEANINGS[self.code]

    def file_mode(self) -> None:
        # Loads the file we have and stores its size
        self.file = open(self.response_file, 'rb')
        self.file_size = os.path.getsize(self.response_file)
    
    def read_file_bytes(self, n : int) -> bytes:
        # Reads x bytes from our loaded file, returns '' if the file is done being read''

        read_bytes = self.file.read(n) # Read n bytes

        if read_bytes == b"": #EOF, stop and close file
            self.file.close()
            return read_bytes
        else:
            return read_bytes


r = []
w = []
x = []
input_sockets = []
output_sockets = []

response_messages = {} # Keeps track of previous messages to test for a double new-line
request_message = {} 
ongoing_requests = {} # A dictionary of holds a request object for THE REQUEST WE ARE WORKING ON CURRENTLY
outgoing_responses = {} # A dictionary of queues that hold request objects for RESPONSES READY TO OUTPUT
socket_addresses = {} # Dictionary holding (HOST, PORT) tuples for all sockets that are active
outgoing_file = {} # A dictionary that has a socket key and a value for a HTTPResponse object that has the file we are sending
rest_time = {} # a dictionary that lets me keep track of how long a socket has been idle for

server : socket.socket


def new_client_socket(new_s : socket.socket, addr):
    
    # Initalize client socket and add to input list
    new_s.setblocking(0)
    input_sockets.append(new_s)
    
    # Add to auxillary lists
    socket_addresses[new_s] = addr
    rest_time[new_s] = 0.0
    response_messages[new_s] = ""
    ongoing_requests[new_s] = None
    outgoing_file[new_s] = None
    outgoing_responses[new_s] = queue.SimpleQueue()


def close_socket(s : socket.socket):
    # Remove socket from socketlist and close
    if s in input_sockets:
        input_sockets.remove(s)
    if s in output_sockets:
        output_sockets.remove(s)
    if outgoing_file[s] != None:
        if outgoing_file[s].file != None:
            outgoing_file[s].file.close()

    rest_time.pop(s)
    outgoing_file.pop(s)
    socket_addresses.pop(s)
    response_messages.pop(s)
    ongoing_requests.pop(s)
    outgoing_responses.pop(s)

    s.close()


def make_responses(req_list : list, s : socket.socket) -> None:
    # Takes a list of requests from a socket and generates responses for them
    # TODO: Add 404 test


    for req in req_list:
        
        # Before we make a response, make sure our headers are applied
        req.apply_headers()

        # Test for 400 responses
        if not req.is_valid_request():
            outgoing_responses[s].put_nowait(HTTPResponse(400, req))
        elif not req.file_exists():
            outgoing_responses[s].put_nowait(HTTPResponse(404, req))
        else:
            ok_resp = HTTPResponse(200, req)
            ok_resp.file_size = os.path.getsize(ok_resp.response_file)
            outgoing_responses[s].put_nowait(ok_resp)


def partial_request(s : socket.socket, request : str) :
    # Gets a partial request and makes/adds to a response to it

    # No partial request started yet for this socket, make one!
    if ongoing_requests[s] == None:
        ongoing_requests[s] = HTTPRequest(request)

        if ongoing_requests[s].is_valid_command(): 
            # We're good!
            pass
        else:
            # We are not good! Kill this connection at once!
            outgoing_responses[s].put_nowait(HTTPResponse(400, HTTPRequest(request)))
            
    else: # Add info to current incomplete request
        ongoing_requests[s].add_new_headers(request)


def full_request(s : socket.socket, request : str) :
    # Handles recieving a request that has double new-line characters
    # This includes requests that have no new data, as well as requests with more than one full request
    # This function gets the request data, gets any needed info, and then sends all requests for the given socket have responses generated

    # Due to abgiduity in new line characters, we have to split twice
    # Using a | character makes the split function act up, so I had to resort to this
    split_unix = re.split(r"\r\n\r\n", request)
    split_windows = re.split(r"\n\n", request)

    if len(split_unix) > len(split_windows):
        split_request = split_unix
    else:
        split_request = split_windows
    
    request_strings = []

    # Get any ongoing requests, we must finish them

    ongoing = ongoing_requests[s]
    
    for i in range(0, len(split_request) - 1, 1):

        request_strings.append(split_request[i].lstrip())

    # If the last request is empty, ignore it, else, make a partial request with it
    if split_request[-1] == '':
        ongoing_requests[s] = None # No more partial requests
    else:
        ongoing_requests[s] = HTTPRequest(split_request[-1].lstrip()) # Make paretial request of last request line

    # Alright, sweet. Now we have a bunch of requests we can process.
    # First, lets make sure we finish that first ongoing request we had (if it existed)

    requests = []
    requests_done = 0

    if ongoing == None:
        pass
    else:
        if len(request_strings) != 0:
            ongoing.add_new_headers(request_strings[0])
        requests.append(ongoing)
        requests_done += 1
    
    # Cool, now we just make full requests out of the other request strings

    while requests_done < len(request_strings):
        requests.append(HTTPRequest(request_strings[requests_done]))
        requests_done += 1
    
    # YEAH! We now have our make_responses(requests, s)requests in a nice list, time to respond to them!
    
    make_responses(requests, s)


def read_socket(s : socket.socket):
    # TODO: Make actual code, we are just showing that a connecting was established rn

    if s == server:
        # If we are reading from the server, open the new connection, respond and then close the connection
        conn, addr = s.accept()
        new_client_socket(conn, addr)
    
    elif not s in output_sockets:
        # This is a client socket, get the message from it IF WE CAN
        client_message : str = s.recv(1024)

        if client_message == None: # Error getting message, close socket
            print("ERROR GETTING CLIENT MESSAGE")
            close_socket(s)
        
        else: # Got a message!
            
            decoded = client_message.decode()
            response_messages[s] += decoded # response_messages is used soley to track when we encounter /n/n or /n/r/n/r

            if re.search(r"\r\n\r\n", response_messages[s]) == None and re.search(r"\n\n", response_messages[s]) == None: # This is a partial request
                # No terminating chars, add onto/make partial request
                partial_request(s, decoded)

            else: # This contains at least one full request, parse it
                full_request(s, decoded)
                response_messages[s] = ""

        # If we have anything to respond with, do that! (Remove from input sockets until needed again)
        if not outgoing_responses[s].empty() and not s in output_sockets:
            output_sockets.append(s)


def writing_socket(s : socket.socket):

    # Only stop resoinding when there are no more responses
    if outgoing_responses[s].empty() and outgoing_file[s] == None:
        close_socket(s)

    elif outgoing_file[s] == None: # No currently outgoing file
        ## Send response message
        response = outgoing_responses[s].get_nowait()
        response_address = socket_addresses[s]

        # Log response
        print(response.get_current_datetime() + ": " + str(response_address[0]) + ":" + str(response_address[1]) + " " + response.request_command.rstrip() + "; " + response.get_response_message())
        
        # Make header
        header_resp = "\r\nConnection: " + response.connection_type + "\r\nContent-Length: " + str(response.file_size) + "\r\n\r\n"

        s.send((response.get_response_message() + header_resp).encode())

        response_messages[s] = ""

        # If the previous response was a 400 response, close the connection and halt immideately
        if response.code == 400:
            close_socket(s)
            return
        
        # If we have a file to start sending, send it
        if response.response_file != None:
            response.file_mode() # Load the file we want to start sending
            outgoing_file[s] = response

        elif response.connection_type == 'close': # If we have no file after this and need to close, close
            close_socket(s)
        
        elif outgoing_responses[s].empty(): # Check if this is the last request, if so, remove this socket from the output pool
            output_sockets.remove(s)

    else:
        # Currently outgoing file
        
        # First, get the needed data
        file_bytes : bytes = outgoing_file[s].read_file_bytes(SEND_BUFFER)

        s.send(file_bytes)

        if file_bytes == b'': # No more data to send, EOF
            
            # If this request isn't keep-alive, close the socket
            if outgoing_file[s].connection_type == 'close':
                close_socket(s)
            else: # If this is keep-alive just send the file
                outgoing_file[s] = None
            
            


            

def error_socket(s : socket.socket):

    # Close socket and remove from socket list
    print("SOCKET CLOSED DUE TO ERROR")
    close_socket(s)


# Create server socket and configure it
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.setblocking(0)

args = sys.argv
s_host = DEFAULTHOST
s_port = DEFAULTPORT

if len(args) >= 3:
    s_host = args[1]
    s_port = int(args[2])
else:
    print("NO IP AND/OR PORT PROVIDED, USING DEFAULT HOST AND PORT: " + str((DEFAULTHOST, DEFAULTPORT)))

server.bind((s_host, s_port))
server.listen(20)

input_sockets.append(server)
socket_addresses[server] = (s_host, s_port)

# Start the server loop
print("Starting SWS Server Loop")
while True:

    select_time_start = time.time() # Get start time

    r, w, x = select.select(input_sockets, output_sockets, input_sockets, SELECT_TIMEOUT) # Get sockets to service

    for si in input_sockets:
        if si in x:
            error_socket(si)
        elif si in r and not si in w: # We dont want to read from sockets we have to send stuff to
            read_socket(si)
    
    for sw in output_sockets:
        if si in x:
            error_socket(sw)
        elif si in w:
            writing_socket(sw)
    
    select_end_time = time.time()
    delta = select_end_time - select_time_start

    for s in input_sockets:
        if s == server:
            continue
        
        if not s in r and not s in w:

            rest_time[s] += delta
            if rest_time[s] >= UNTIL_TIMEOUT:
                print(f"Client socket of address {socket_addresses[s]} has timed out after {rest_time[s]} seconds")
                close_socket(s)
        else:
            rest_time[s] = 0.0 # Reset timer

s.close()