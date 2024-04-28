import urequests as requests
import json
import time
import network
from time import sleep
import gc
import uzlib
from io import BytesIO
import ntptime
import machine
from machine import Pin, I2C, PWM
import ssd1306  # <<--- New import
from font import Font  # <<--- New import
import re
import config
from config import ALERT_ROI, WIFI_KEYS
import _thread

thread_counter = 0
# Initialize the buzzer <<--- New code block
buzzer = PWM(Pin(12))
BUZZER_GND = Pin(26, Pin.OUT)
boot = Pin(0, Pin.IN)
test = Pin(2, Pin.IN)
TEST_GND = Pin(15, Pin.OUT)
text_pos = [5, 30]
text_pos_index = 0

thread_limit = 2
max_retries = 8  # Max number of attempts to initialize the display
general_alerts_counter = 0
# Create a global lock
alert_sound_lock = _thread.allocate_lock()
print_text_lock = _thread.allocate_lock()
log_lock = _thread.allocate_lock()
rolling_text_lock = _thread.allocate_lock()
progressbar_lock = _thread.allocate_lock()
start_processor_lock = _thread.allocate_lock()
text_slider_lock=_thread.allocate_lock()

alert_data = {}
city_data = []
migun_time = 0

# Create a list to serve as our queue to store print_text arguments
print_queue = []

# Flag to control the print processor thread
run_print_processor = True

# Variables to keep track of the progress bar position and length
PROGRESSBAR_LENGTH = 60  # Length of the progress bar
PROGRESSBAR_X_START = 10  # X coordinate where the progress bar starts
PROGRESSBAR_Y_START = 30  # Y coordinate where the progress bar starts


# The single wrapper function
MAX_THREADS = 5  # Example maximum
active_threads = {}
thread_id_counter = 0  # To assign unique IDs to threads
thread_dict_lock = _thread.allocate_lock()  # Lock for thread-safe dictionary operations

# Global station variable
station = network.WLAN(network.STA_IF)
# Set the width and height of the display
WIDTH = 128
HEIGHT = 64

wifi_connected = 0

for attempt in range(1, max_retries + 1):
    try:
        # Initialize the display  <<--- New code block
        i2c = I2C(scl=Pin(22), sda=Pin(21), freq=4000000)
        display = ssd1306.SSD1306_I2C(WIDTH, HEIGHT, i2c)
        f = Font(display)
        print("Successfully initialized the display!")
        break  # Exit the loop if successful
    except Exception as e:
        print(f"Attempt {attempt}: Failed to initialize the display. Error: {e}")
        if attempt == max_retries:
            print("Max attempts reached. Giving up.")
        else:
            print("Retrying...")
            time.sleep(1)  # Wait for 1 second before retrying



def timestamp_str():
    return '{}-{}-{}  {:02d}:{:02d}:{:02d}'.format(dt[2], dt[1], dt[0], dt[3], dt[4], dt[5])


def start_thread_with_limit(target, caller, args=()):
    global active_threads, thread_id_counter

    def thread_limiter(thread_id):
        global active_threads
        # Start the actual thread task
        target(*args)
        # Remove thread from active_threads when done
        with thread_dict_lock:
            del active_threads[thread_id]

    print(f'pre-call active threads: {len(active_threads)}, caller: {caller}, target: {target.__name__}')

    with thread_dict_lock:
        if len(active_threads) < MAX_THREADS:
            thread_id = thread_id_counter
            thread_id_counter += 1
            _thread.start_new_thread(thread_limiter, (thread_id,))
            active_threads[thread_id] = target.__name__
            # print(f'post-call active threads: {len(active_threads)}')
        else:
            print("Maximum thread limit reached. Cannot create new thread.")
            print_active_threads()

def print_active_threads():
    with thread_dict_lock:
        for thread_id, name in active_threads.items():
            print("Thread ID:", thread_id, "Name:", name)
            
            
def check_wifi_and_display_x():
    global wifi_connected
    if not station.isconnected():
        # Coordinates for the top right corner
        x_start = WIDTH - 10
        y_start = 2
        if wifi_connected == 1:  # redraw only if status changes
            # Draw WiFi icon in the top right corner
            display.line(x_start, y_start + 18, x_start + 8, y_start + 10, 0)  # Diagonal top
            display.line(x_start, y_start + 10, x_start + 8, y_start + 18, 0)  # Diagonal bottom

            display.show()
            wifi_connected = 0
    else:
        x_start = WIDTH - 10
        y_start = 2
        if wifi_connected == 0:  # Only update if the status has changed
            # Clear the "X" if connected by redrawing in background color
            display.line(x_start, y_start + 18, x_start + 8, y_start + 10, 1)  # Diagonal top
            display.line(x_start, y_start + 10, x_start + 8, y_start + 18, 1)  # Diagonal bottom
            # Draw WiFi icon to indicate connection
            # Draw arcs to represent the wifi signal
            display.show()
            wifi_connected = 1


def log_and_print(*args):
    start_thread_with_limit(log_and_print_execution, "log_and_print", args)


def log_and_print_execution(*args):
    # Check if 'silent' is in the arguments
    silent_mode = 'silent' in args
    if silent_mode:
        # Remove 'silent' from args if present
        args = tuple(arg for arg in args if arg != 'silent')

    # Convert the arguments to string and join them
    date = timestamp_str() + "\t"
    log_string = ' '.join(map(str, args))
    log_string = date + log_string

    # Acquire the lock
    log_lock.acquire()
    try:
        # Append to the log.txt file
        with open('log.txt', 'a') as f:
            f.write(log_string + '\n')

        # Print the string to the terminal only if not in silent mode
        if not silent_mode:
            print(log_string)
    finally:
        # Release the lock
        log_lock.release()


def print_text_processor():
    global dt

    while run_print_processor:
        # Try to acquire the lock without blocking
        if text_slider_lock.acquire(False):
            try:
                # Critical section - lock acquired
                if print_queue:
                    # Get the arguments from the queue
                    name, row_height = print_queue.pop(0)

                    for x in range(128, -len(name) * 8, -1):
                        display.fill(0)
                        display.text(name, x, row_height, 1)
                        display_counter()
                        display_thread_counter()
                        display.show()
                        sleep(0.001)
                    print("{:02d}:{:02d}:{:02d}".format(dt[3], dt[4], dt[5]))
            finally:
                # Release the lock
                text_slider_lock.release()
        else:
            # Could not acquire the lock, skip this iteration
            sleep(0.1)  # Optional: sleep for a bit before trying again


def display_queue_in_cells():
    global text_pos_index
    global print_queue
    TABLE_RECT_HEIGHT = 38  # Width and height for the area
    CELL_WIDTH, CELL_HEIGHT = WIDTH // 2, TABLE_RECT_HEIGHT // 2
    
    
    
    if len(print_queue):
        # print("exit 148")
        return

    # Try to acquire the lock without blocking
    if not rolling_text_lock.acquire(False):
        # Could not acquire the lock, exit the function or handle as needed
        print("Could not acquire lock, exiting")
        return

    try:


        def display_item(x, y, item):
            display.fill_rect(x, y, CELL_WIDTH, CELL_HEIGHT, 0)  # Clear cell
            display.text(item, x, y, 1)  # Display item
            display.show()
        
        cycle_count = 3  # Number of times to cycle through the queue
        display.fill_rect(0,8,128,38,0)
        for _ in range(cycle_count):
            start_index = 0
            while start_index < len(print_queue):
                for row in range(2):
                    for col in range(2):
                        index = start_index + row * 2 + col
                        if index < len(print_queue):
                            x = col * CELL_WIDTH
                            y = 8 + row * CELL_HEIGHT  # Start from y=8 as per your requirement
                            display_item(x, y, print_queue[index])
                        else:
                            break

                start_index += 4  # Move to the next set of 4 items
                time.sleep(2)  # Wait for 2 seconds before showing the next set
    
    finally:
        # Release the lock
        rolling_text_lock.release()
    
    print_queue=[]
    return
    
    

def print_text_rolling(name="■", row_height=0):
    global text_pos_index
    global print_queue
    if name=="■":
        # Ensure that all characters in the item are ASCII (English characters)
        filtered_items = [item for item in ALERT_ROI if all(ord(char) < 128 for char in item)]

        name=f"■ monitoring: {filtered_items}"
    if len(print_queue):
        # print("exit 148")
        return

    # Try to acquire the lock without blocking
    if not rolling_text_lock.acquire(False):
        # Could not acquire the lock, exit the function or handle as needed
        print("Could not acquire lock, exiting")
        return

    try:
        # Critical section - lock acquired
        previous_x = 128

        for x in range(128, -len(name) * 8, -1):
            if len(print_queue):
                print("early exit 162")
                break
            clear_area(0, 0, WIDTH, 8)
            display.text(name, x, row_height, 1)
            display.show()
            previous_x = x
            sleep(0.001)
        sleep(1)
    finally:
        # Release the lock
        rolling_text_lock.release()


def clear_area(x, y, width, height):
    """Clear a specific area of the display."""
    display.fill_rect(x, y, width, height, 0)


def print_text_static(name, row_height=0):
    global text_pos_index  # Declare text_pos_index as global to modify it
    if len(print_queue):
        return

    display.fill(0)  # Clear the display
    display_counter()
    display_thread_counter()

    display.text(name, text_pos[int(dt[5]) % 2], text_pos[0], 1)  # Display the name
    display.text("{:02d}:{:02d}:{:02d}".format(dt[3], dt[4], dt[5]), 10, 50, 1)  # Display the name
    # print("491 {:02d}:{:02d}:{:02d}".format(dt[3], dt[4], dt[5]))
    display.show()  # Refresh the display

    # Toggle text_pos_index for the next call
    # text_pos_index = text_pos_index+1

    # sleep(1)  # Pause for 1 second before the next name


"""
def update_progress_bar():
    global text_pos_index
    global dt
    global thread_counter

    if len(print_queue):
        return
    # Clear the area where the progress bar will be displayed
    # display.fill_rect(PROGRESSBAR_X_START, PROGRESSBAR_Y_START, PROGRESSBAR_LENGTH, 10, 0)
    with progressbar_lock:
        progress_position = int(dt[5]) % PROGRESSBAR_LENGTH
        thread_counter = int((progress_position / 60) * 100)
        clear_area(PROGRESSBAR_X_START, PROGRESSBAR_Y_START, PROGRESSBAR_LENGTH, 8)
        display.fill_rect(PROGRESSBAR_X_START, PROGRESSBAR_Y_START, progress_position, 8, 1)
        display.show()
"""


def update_progress_block():  # block that slides along to indicate progress, but not changing its sized
    return
    global text_pos_index
    global dt
    global thread_counter

    if len(print_queue):
        return
    # if not progressbar_lock.acquire(0):
    #   print("exit 226")
    #   return

    # Clear the area where the progress bar will be displayed
    # display.fill_rect(PROGRESSBAR_X_START, PROGRESSBAR_Y_START, PROGRESSBAR_LENGTH, 10, 0)
    with progressbar_lock:
        #    print("in lock 239", end=' ')
        progress_position = 2 * int(dt[5]) % 60
        thread_counter = int((progress_position / 60) * 100)
        block_position = int((progress_position / 60) * WIDTH)
        # clear_area(PROGRESSBAR_X_START, PROGRESSBAR_Y_START, PROGRESSBAR_LENGTH, 8)
        display.fill_rect(0, 28, WIDTH, 16, 0)
        display.fill_rect(block_position, PROGRESSBAR_Y_START, int(0.1 * WIDTH), 8, 1)
        display.show()


# print("out of lock 246")


def enqueue_print_text(name, row_height=0):
    # Add the arguments to the list
    print_queue.append((name, row_height))


def start_print_processor():
    global thread_counter
    with start_processor_lock:  # Acquire the lock
        print("in lock 258", end=' ')
        # Start the print_text_processor thread
        start_thread_with_limit(print_text_processor,"start_print_processor", ())
    print("out of lock 261")


def display_counter():
    global display, f, general_alerts_counter
    # Clear the display
    # Draw the counter on the bottom right corner
    counter_str = str(general_alerts_counter)
    font_width = 10
    font_height = 10
    display.text(counter_str, display.width - font_width - 20, display.height - font_height - 5, 1)  # Display the name
    # Update the display


def display_thread_counter():
    global display, f, thread_counter
    # Clear the display
    # Draw the counter on the bottom right corner
    counter_str = str(thread_counter)
    font_width = 10
    font_height = 10
    display.fill_rect(0, 0, 40, 30, 0)
    display.text(counter_str, 0 + font_width, 0 + 2 * font_height, 1)  # Display the name
    # Update the display


def extract_data_string(json_str):
    start_pos = json_str.find('"data": [')
    data_list = []
    if start_pos != -1:
        start_pos += len('"data": [')
        end_pos = json_str.find('],', start_pos)
        # print(51,start_pos,end_pos)
        trimmed_str = json_str[start_pos:end_pos]

        # Remove quotation marks
        data_str = trimmed_str.replace('"', '')

        # Split the string into a list using comma as the delimiter
        data_list = data_str.split(',')

        # Remove leading and trailing whitespaces from each string in the list
        data_list = [item.strip() for item in data_list]

        # Join the list elements back into a string, separated by a comma and a space
        return ', '.join(data_list)


def connect():
    global station  # Indicate that we're using the global station variable
    global wifi_connected
    keys_ = WIFI_KEYS
    sleep(0.2)
    station.active(True)
    stat = station.scan()
    best_ap = (None, 0, 0, -100)
    SSID = ''
    for s in stat:
        check = s[0].decode('utf-8')
        if check in keys_.keys() and s[3] > best_ap[3]:
            best_ap = s
            print(f'The best Access Point found is {best_ap[0]}')
            SSID = best_ap[0].decode('utf-8')

    if best_ap[0] != None:
        station.connect(SSID, keys_[SSID])
        while station.isconnected() == False:
            pass
        print(f'Connected to {SSID}')
        wifi_connected = 1
    else:
        print("No suitable Access Point found.")


def play_buzz(frequency, duration):
    buzzer.freq(frequency)
    buzzer.duty(512)  # 50% duty cycle
    sleep(duration)
    buzzer.duty(0)  # Turn off


def parse_string(s):
    res = []
    temp = ''
    skip_space = False
    for char in s:
        if char == ',':
            res.append(temp.strip())  # Strip to remove leading/trailing white-spaces
            temp = ''
            skip_space = True
        else:
            if skip_space and char == ' ':
                continue
            skip_space = False
            temp += char
    res.append(temp.strip())
    # print(res)
    return res


class RedAlert():

    def __init__(self):
        # initialize locations list
        # self.locations = self.get_locations_list()
        # cookies
        self.cookies = ""
        # initialize user agent for web requests
        self.headers = {
            "Host": "www.oref.org.il",
            "Connection": "close",  # changed
            "Content-Type": "application/json",
            "charset": "utf-8",
            "X-Requested-With": "XMLHttpRequest",
            "sec-ch-ua-mobile": "?0",
            "User-Agent": "",
            "sec-ch-ua-platform": "macOS",
            "Accept": "*/*",  # changed
            "sec-ch-ua": '".Not/A)Brand"v="99", "Google Chrome";v="103", "Chromium";v="103"',
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": "https://www.oref.org.il/12481-en/Pakar.aspx",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        }
        # intiiate cokies
        self.get_cookies()
        self.csv_data = None
        self.enomem_error_count = 0  # Initialize the counter in your class __init__ method

    def get_cookies(self):
        HOST = "https://www.oref.org.il/"
        r = requests.get(HOST, headers=self.headers)

        try:
            self.cookies = r.cookies
        except:
            pass
            # print('No cookies item found')
        # print('Done')

    def count_alerts(self, alerts_data):
        # this function literally return how many alerts there are currently
        return len(alerts_data)

    def find_city(self, city_name):
        file = self.csv_data
        with open('minimized_cities_data.csv', 'r') as file:
            # print(type(file))
            # Read the headers first to get the column names
            headers = file.readline().strip().split(',')

            # Loop through each line in the CSV
            for line in file:
                row = line.strip().split(',')
                # Check if the city_name matches with the current row's 'heb_name_parsed' column
                if city_name in row[headers.index('heb_name_parsed')]:
                    # Return the row as a dictionary
                    return dict(zip(headers, row))
        # If the city_name is not found, return None
        return {}

    def get_red_alerts(self):
        URL = "https://www.oref.org.il/WarningMessages/alert/alerts.json"
        try:
            r = requests.get(URL, headers=self.headers)
            self.enomem_error_count = 0  # Reset the counter on a new request attempt

            if r.status_code == 200:
                if not r.content:
                    print("Response content is empty")
                    return
                if 'gzip' in r.headers.get('Content-Encoding', ''):
                    try:
                        decompressor = uzlib.DecompIO(BytesIO(r.content), 31)
                        decompressed_data = decompressor.read()
                    except ValueError:
                        print("Error decompressing data. Data might not be compressed or might be corrupt.")
                        return
                else:
                    decompressed_data = r.content

                alerts = decompressed_data.decode('utf-8-sig').strip()
                if len(alerts) <= 1:
                    return ''
                else:
                    return alerts

            else:
                print(f"HTTP error. Status code: {r.status_code}")
                return

        except OSError as e:
            if e.args[0] == 12:  # ENOMEM error code
                self.enomem_error_count += 1
                gc.collect()
                sleep(1)
                print(f"ENOMEM error occurred {self.enomem_error_count} times")
                if self.enomem_error_count >= 8:
                    print("Resetting machine due to repeated ENOMEM errors.")
                    print_active_threads()

                    #machine.reset()
            else:
                # self.enomem_error_count +=1
                gc.collect()
                return ''
                print(f"An OSError occurred that is not ENOMEM: {e}")
        except Exception as e:
            self.enomem_error_count = 0
            # print(f"Network or request error: {e}")
            gc.collect()

    def process_alerts(self, alerts):
        if alerts is None or len(alerts) < 1:
            return ''
        log_and_print(alerts)

        global general_alerts_counter
        global thread_counter
        global dt
        # if test.value() == 1:
        result = alerts
        jsn_dmp = json.dumps(alerts)
        # print(170, jsn_dmp)
        jsn_ld = json.loads(jsn_dmp)
        # print(173, type(jsn_ld), jsn_ld)
        matching_pairs = []
        try:
            parsed_str = extract_data_string(alerts)
            return parsed_str
            timestamp = "{:02d}:{:02d}:{:02d}".format(dt[3], dt[4], dt[5])

            # print(timestamp, " 312 alert recieved :", parsed_str)
            brk_to_list = parse_string(parsed_str)

            # Find matching pairs using list comprehensions
            matching_pairs = [(alert_item, brk_item) for alert_item in ALERT_ROI for brk_item in brk_to_list if
                              alert_item in brk_item]
        except Exception as ex:
            log_and_print("320 error:  ", ex)
            return ''

    def process_alerts_string(self, parsed_str):
        if len(parsed_str) == 0:
            return parsed_str
        global general_alerts_counter
        global thread_counter
        global dt
        timestamp = "{:02d}:{:02d}:{:02d}".format(dt[3], dt[4], dt[5])

        # print(timestamp, " 312 alert recieved :", parsed_str)
        brk_to_list = parse_string(parsed_str)

        # Find matching pairs using list comprehensions
        matching_pairs = [(alert_item, brk_item) for alert_item in ALERT_ROI for brk_item in brk_to_list if
                          alert_item in brk_item]

        # Check and print the matched pairs
        if matching_pairs:
            if len(matching_pairs) <= 1: log_and_print(matching_pairs)

            start_thread_with_limit(alert_sound, "process_alerts_string", ())
            # start_thread_with_limit(print_text, ("RED ALERT", 20))
            # Start the print processor
            start_print_processor()

            # Add text to be printed
            enqueue_print_text("RED ALERT", row_height=20)

            for alert, brk in matching_pairs:
                log_and_print(f"Match found: '{alert}' in '{brk}'")

            for item in brk_to_list:
                try:

                    timestamp = "{:02d}:{:02d}:{:02d}".format(dt[3], dt[4], dt[5])
                    log_and_print(timestamp, " - Alert: ", item, end='  ')
                    en_item = self.find_city(item)
                    log_and_print(timestamp, " - Alert: ", en_item.get('label', item))
                except Exception as ex:
                    log_and_print("347 error:  ", ex)
                try:
                    general_alerts_counter += 1
                    # Start the print processor
                    start_print_processor()
                    # Add text to be printed
                    # enqueue_print_text("RED ALERT", row_height=20)
                    enqueue_print_text(en_item['label'], row_height=20)
                except Exception as ex:
                    log_and_print("358 error:  ", ex)


        elif len(brk_to_list):  # non ROI city
            for item in brk_to_list:
                try:
                    general_alerts_counter += 1
                    play_buzz(200, 0.2)
                    sleep(0.3)
                    timestamp = "{:02d}:{:02d}:{:02d}".format(dt[3], dt[4], dt[5])
                    en_item = self.find_city(item)
                    log_and_print(timestamp, " - Warning: ", en_item.get('label', item))
                    start_print_processor()
                    # Add text to be printed
                    # enqueue_print_text("RED ALERT", row_height=20)
                    enqueue_print_text(en_item['label'], row_height=20)
                except Exception as ex:
                    log_and_print("380 error:  ", ex)
            # except Exception as ex:
            #   print("184 error:  ", ex)
            return brk_to_list

        # if test.value() == 1:
        #   print(163)
        # Create a Python dictionary
        # Convert the dictionary to a JSON string
        #  alert_sound()
        if len(parsed_str) == 0:
            return []
        else:
            start_thread_with_limit(display_queue_in_cells, "main 2",())
            return parsed_str
        # return [parsed_str]


def adjust_for_israel(dt):
    year, month, day, _, hour, minute, second, _ = dt
    dst_start = (year, 3, (31 - (5 + year * 5 // 4) % 7), 2)  # Friday before last Sunday in March at 2am
    dst_end = (year, 10, (31 - (2 + year * 5 // 4) % 7), 2)  # Last Sunday in October at 2am
    is_dst = dst_start <= (year, month, day, hour) < dst_end

    # Adjust for timezone and DST
    hour += 2 + is_dst  # UTC+2 for IST and +1 for DST

    # Handle overflow
    if hour >= 24:
        hour -= 24
        day += 1
        # Handle day overflow for each month
        if month in [4, 6, 9, 11] and day > 30 or month == 2 and (
                day > 29 or (year % 4 != 0 or (year % 100 == 0 and year % 400 != 0)) and day > 28) or day > 31:
            day = 1
            month += 1
            if month > 12:
                month = 1
                year += 1

    return (year, month, day, hour, minute, second)


# ******************************************************

for attempt in range(1, max_retries + 1):
    try:
        # Initialize the display  <<--- New code block
        connect()

        ntptime.settime()  # This will set the board's RTC using an NTP server

        rtc = machine.RTC()
        print("Successfully initialized the display!")
        break  # Exit the loop if successful
    except Exception as e:
        print(f"Attempt {attempt}: Failed to initialize the display. Error: {e}")
        if attempt == max_retries:
            print("Max attempts reached. Giving up.")
        else:
            print("Retrying...")
            time.sleep(1)  # Wait for 1 second before retrying

BUZZER_GND.value(0)  # Set to low to behave like GND
TEST_GND.value(1)  # Set to low to behave like GND
sleep(0.3)
# Adding a polling flag and a counter
poll_for_alerts = True
counter = 0


def display_data(json_str):
    try:
        # Parse JSON string to Python dictionary
        json_dict = json.loads(json_str)

        # print(type(json_dict))  # Debug: print the type of json_dict
        print(json_dict)  # Debug: print the content of json_dict

        # Extract list of strings from "data" key
        data_list = json_dict['data']

        # Additional debugging: Print the type and content of data_list
        print(type(data_list))  # Debug: print the type of data_list
        print(data_list)  # Debug: print the content of data_list

        # Clear the display
        display.fill(0)

        # Initialize variables to manage positions
        x = 0
        y = 0

        # Loop through the list of strings and print them
        for i, item in enumerate(data_list):
            display.text(item, x, y, 1)

            # Manage positions for 2 columns
            if i % 2 == 0:
                x = 64  # Move to the second column
            else:
                x = 0  # Move back to the first column
                y += 10  # Move down to the next row

        # Refresh the display
        display.show()
        sleep(1)

    except (ValueError, KeyError, TypeError):
        # If JSON is invalid or doesn't have a "data" key, call print_text_static
        # print_text_static("Invalid JSON")
        pass


def alert_sound():
    global thread_counter  # Declare global at the beginning

    # Acquire the lock before doing anything else
    with alert_sound_lock:
        thread_counter += 1
        try:
            play_buzz(800, 0.5)
            sleep(0.3)
            play_buzz(1200, 0.5)
            sleep(0.3)
            play_buzz(800, 0.5)
            sleep(0.3)
            play_buzz(1200, 0.5)
            sleep(0.3)
        finally:
            thread_counter -= 1

            # Release the lock, so other threads can call this function
            # alert_sound_lock.release()


def print_if_not_none(*elements):
    try:
        for element in elements:
            if element is None:
                return
            if isinstance(element, str):
                if element is not None and len(element) > 0:
                    print(element)
    except Exception as ex:
        return


if __name__ == "__main__":
    alert = RedAlert()
    gc.collect()

    thread_counter = 0
    start_thread_with_limit(alert_sound, "main",())
    log_and_print("Init")
    text_pos_index = 1

    while True:
        #display.fill_rect(0, 8, 40, 38, 1)
        #display.fill_rect(44, 8, 40, 38, 0)
        #display.fill_rect(84, 8, 40, 38, 1)
        #display.fill_rect(126, 15, 2, 20, 1)
        if not boot.value():
            break
        
        if test.value():
            print_queue=["0","1","2","3","4","5","6","7"]
            
            display_queue_in_cells()
            print(f'len queue {len(print_queue)}')
            print_queue=[]
        
        try:
            if poll_for_alerts:
                dt = adjust_for_israel(rtc.datetime())
                # update_progress_bar()
                update_progress_block()
                # print("{:02d}:{:02d}:{:02d}".format(dt[3], dt[4], dt[5]))
                display_counter()
                display_thread_counter()
                try:
                    alerts_result = alert.get_red_alerts()
                    # print_if_not_none(589, alerts_result)
                    alerts_result_string = alert.process_alerts(alerts_result)
                    # print_if_not_none(591, alerts_result_string)
                    list_of_alerts = alert.process_alerts_string(alerts_result_string)
                    print_if_not_none(593, list_of_alerts)
                    # Call the function to check the connection and display an "X" if not connected
                    check_wifi_and_display_x()
                except Exception as ex:
                    print(606, ex)
                    gc.collect()
                    continue
                #        print(359, type(alerts_result))
                try:
                    # First parse to convert escaped string into regular string
                    intermediate_str = json.loads(alerts_result)

                    json_data = json.loads(intermediate_str)

                    keys = json_data.keys()
                    # print("Keys in the JSON object:", keys)
                    # print(397, json_data['data'])
                    if len(alerts_result) > 2:
                        print("alert 456")
                        # alert_sound()
                        display_data(alerts_result)

                    if 'data' in json_data:
                        print("dont remove 487")
                        # print(306, json_data['data'])
                        name_str = ', '.join(json_data['data'])
                        print(extract_data_string(alerts_result))
                        print("Cities: ", name_str)
                        alert_sound()
                        display_data(name_str)
                        # print_text(name_str)
                        poll_for_alerts = False  # Stop polling once we get data

                except (ValueError, KeyError, TypeError, AttributeError) as ex:
                    # Handle exceptions here
                    try:
                        if len(alerts_result) + len(alerts_result_string) + len(list_of_alerts) > 0:
                            log_and_print("silent","error 644", f'alerts_result: {alerts_result}',
                                  f'alerts_result_string: {alerts_result_string}', f'list_of_alerts:  {list_of_alerts}')
                    except:
                        gc.collect()
                        pass
                    print_text_static("")  # clock
                    start_thread_with_limit(print_text_rolling, "main 2",())
                    

                    display_counter()
                    display_thread_counter()
                    # print(".", end='')
                    # print(ex)


                except (OSError):  # MicroPython may raise OSError for JSON issues
                    # print_text_static("ALL CLEAR")
                    start_thread_with_limit(print_text_rolling, "main 3", ())
                    

                    pass

            else:
                # When the counter reaches a certain value, start polling again
                counter += 1
                if counter >= 5:  # Adjust the value as needed
                    poll_for_alerts = True
                    counter = 0
        except OSError as e:
            # global station
            print(566, e)
            if station.isconnected():
                print("An OS error occurred, but the ESP32 is still connected to the internet:", e)
            else:
                print("An OS error occurred and the ESP32 is NOT connected to the internet:", e)
            gc.collect()

        sleep(0.1)  # Sleep for a shorter time to keep things responsive






