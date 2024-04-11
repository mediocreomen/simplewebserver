A simple web server that uses HTTP/1.1\
Written in Python 4 for a thrid-year university networking class I was in

## Usage
The folder that the server is put in will be the root folder of the server.\
Run the server with `sws.py <host_ip> <port_num>`\
If no ip and/or port is provided, the server will default to **localhost** on port **80**\
Press Ctrl+C in the terminal window to terminate the program\

## Things it does
You send it a valid HTTP/1.0 request and it will respond with one of the following:

* HTTP/1.0 200 OK
* HTTP/1.0 400 Bad Request
* HTTP/1.0 404 Not Found\
Along with the correct headers for said response

This is enough for most modern browsers to connect to and get a HTML file from it.\
I have only tested with Firefox and Edge but both were able to request and recieve an HTML file.\

## Things it does not do
Anything else