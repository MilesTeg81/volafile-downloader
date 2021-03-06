
import os
import re
import sys
import time
import config
import logging
import humanfriendly

from enum import Enum
from utils import download_file, log, sanitize_file_name, prepare_url, \
    get_file_id_and_name, get_file_extension, expiration_to_date, \
    log_file, get_logged_files
from selenium import webdriver
from selenium.webdriver.support import ui
from selenium.common.exceptions import TimeoutException
from datetime import datetime


class Result(Enum):
    SUCCESS = 1
    ERROR = 2


class Downloader():

    PASSWORD_INPUT_CSS_SELECTOR = """#room_content_fixed """ \
        """div.ui_frame_container div.ui_frame_body.ui_frame_body_bar """ \
        """input[type="password"]"""

    PASSWORD_BUTTON_XPATH = """//*[@id="room_content_fixed"]/div[1]/div/""" \
        """div[3]/span[2]"""

    MODAL_18_WARNING_XPATH = """//*[@id="room_content_fixed"]/div""" \
        """[1]/div/div[3]/span[2]"""

    def __init__(self,
                 room,
                 password,
                 output_dir,
                 max_allowed_size=config.max_allowed_size,
                 do_log=True,
                 archive=config.archive,
                 archive_type=config.archive_type,
                 chat_log=config.chat_log):

        self.logger = logging.getLogger("root")
        self.driver = None
        self.looping = False
        self.room = room
        self.password = password
        self.output_dir = output_dir
        self.max_allowed_size = max_allowed_size
        self.do_log = do_log
        self.archive = archive
        self.archive_type = archive_type
        self.chat_log = chat_log
        self.old_chat_messages = []

        self.logger.info('Initializing...')
        self.logger.info("Room: %s" % (self.room))
        self.logger.info("Password: %s" % (self.password))
        self.logger.info("Archive: %s" % (self.archive))

        # Create necessary directories
        self.download_directory = os.path.join(self.output_dir, self.room)
        if not os.path.exists(self.download_directory):
            os.makedirs(self.download_directory)

        self.downloaded_files = get_logged_files(self.download_directory)

    def downloadLoop(self, loop_delay=60):
        self.looping = True

        self.initDriver()

        try:
            while self.looping:
                self.logger.info("Downloading room '%s'" % (self.room))

                downloaded = self.downloadFiles(False)

                if self.chat_log:
                    self.downloadChatLog()

                if not downloaded:
                    self.logger.info("There is no files to download")

                self.logger.info("[Sleeping for %s seconds]" % (loop_delay))
                time.sleep(int(loop_delay))
        except Exception:
            self.closeDriver()
            self.logger.warning("Something went wrong, restarting...")
            self.logger.info("[Sleeping for %s seconds]" % (loop_delay))
            time.sleep(int(loop_delay))
            return self.downloadLoop(loop_delay)

        self.closeDriver()

    def download(self):
        """ Download all the files from an entire room """

        self.initDriver()
        self.downloadFiles(True)

    def downloadFiles(self, close_driver=True):
        try:
            # List of files
            self.logger.info("Downloading the list of files...")

            result, files = self.getFilesList()
            if result == Result.ERROR:
                self.logger.error("Error while trying to fetch the list "
                                  "of files, maybe there is no files "
                                  "to download")
                return False

            self.logger.info("List of files downloaded")

            if close_driver:
                self.closeDriver()

        except Exception as ex:
            self.logger.warning(
                "The Website might be offline or another error "
                "occurred: " + str(ex))

            if close_driver:
                self.closeDriver()

            return False

        file_index = 1
        info = dict(
            total=len(files),
            downloaded=0,
            already_exist=0,
            too_big=0,
            failed=0,
            forbidden_extension=0,
            user_ignored=0
        )

        for f in files:

            self.logger.info(u"[%s of %s] [%s] [%s] [%s] [%s] [by %s]" % (
                file_index,
                info["total"],
                f["name"],
                f["extension"],
                humanfriendly.format_size(f["size"]),
                f["expiration"],
                f["tag"]
            ))

            download_directory_path = self.download_directory

            # Change directory if it's to archive
            if self.archive:
                archive_dir_name = datetime.now().strftime(config.archive_date_format)

                if self.archive_type == "CREATION_DATE":
                    archive_dir_name = expiration_to_date(f["expiration"])\
                        .strftime(config.archive_date_format)

                download_directory_path = os.path.join(
                    self.download_directory, archive_dir_name)

            if not os.path.exists(download_directory_path):
                os.makedirs(download_directory_path)
            #quickanddirty edit to remove file-id. less safe, but reduces duplicates and bandwidth -miles
            #file_id_name = f["name"] + " - " + str(f["id"]) + f["extension"]
            file_id_name = f["name"] + f["extension"]
            
            file_path = os.path.join(download_directory_path, file_id_name)

            file_index += 1

            # Check if the file already exists
            if os.path.exists(file_path) or \
                    file_id_name in self.downloaded_files:
                self.logger.info("File already exists")
                info["already_exist"] += 1
                continue

            # Check if we can download a file from this user
            if f["tag"].strip() in config.download_users_to_ignore:
                self.logger.info("User ignored (from the list in the config)")
                info["user_ignored"] += 1
                continue

            # Check if the file extension is blacklisted
            if f["extension"] in config.extensions_blacklist:
                self.logger.warning(
                    "File Extension not allowed to download")
                info["forbidden_extension"] += 1
                continue

            # Check if the file name is blacklisted
            if f["name"] + f["extension"] in config.filenames_blacklist:
                self.logger.warning(
                    "File Name not allowed to download")
                info["forbidden_extension"] += 1
                continue

            # Check if the file size is greater then allowed
            if f["size"] > self.max_allowed_size:
                self.logger.warning(
                    "File size not allowed to download")
                if self.do_log:
                    log("TOOBIG", self.download_directory, f)
                info["too_big"] += 1
                continue

            try:
                self.logger.info("Downloading...")
                download_file(f["url"], file_path)
                self.logger.info("Downloaded")

                self.downloaded_files.append(file_id_name)
                log_file(file_id_name, self.download_directory)

                if self.do_log:
                    log("ARCHIVE", self.download_directory, f)

                info["downloaded"] += 1
            except Exception as ex:
                self.logger.error(
                    "Error downloading file:" + str(ex))
                if self.do_log:
                    log("ERROR", self.download_directory, f)
                info["failed"] += 1

        self.logger.info("DONE")
        self.logger.info("%s of %s Files downloaded" %
                         (info["downloaded"], info["total"]))
        self.logger.info("%s of %s Files already existed" %
                         (info["already_exist"], info["total"]))
        self.logger.info("%s of %s Files were too big to download" %
                         (info["too_big"], info["total"]))
        self.logger.info("%s of %s Files have extensions or name not allowed to "
                         "download" %
                         (info["forbidden_extension"], info["total"]))
        self.logger.info("%s of %s Files couldn't be downloaded (error "
                         "downloading)" %
                         (info["failed"], info["total"]))
        self.logger.info("%s of %s Files couldn't be downloaded (user "
                         "ignored)" %
                         (info["user_ignored"], info["total"]))
        return True

    def initDriver(self):
        if config.driver_path and not os.path.exists(config.driver_path):
            self.logger.error("The driver path in the config doesn't exist")
            print(
                "You can download the chromium drivers from their official"
                "website:"
                "\n\t- Access http://chromedriver.chromium.org/downloads and "
                "download the drivers."
                "\n\t- Place the drivers in the drivers folder or in other place"
                "\n\t- If you are on linux or macOS you might need to give "
                "\n\t  permission to that file, ex: sudo chmod +x chromedriver"
                "\n\t- Edit the 'driver_path' in the config.py file with the path"
                "of the drivers you downloaded"
            )
            sys.exit(1)

        # Create driver with all the arguments
        options = webdriver.ChromeOptions()
        options.add_argument("--log-level=%d" % int(config.driver_log_level))
        options.add_argument("--disable-logging")
        options.add_argument("--disable-extensions")
        if config.driver_headless:
            options.add_argument("headless")

        self.driver = webdriver.Chrome(config.driver_path,
                                       service_log_path="NUL",
                                       chrome_options=options)

        wait = ui.WebDriverWait(self.driver, 3)

        # Go to the url
        self.driver.get(prepare_url(config.base_url, self.room))

        # See if is asking for a password, if yes then type one
        if not self.typePasswordIfNeeded(self.password):
            return (Result.ERROR, None)

        # Try to wait for the +18 warning modal and click OK
        try:
            wait.until(lambda driver: driver.find_element_by_xpath(
                self.MODAL_18_WARNING_XPATH)
            ).click()
        except TimeoutException:
            self.logger.info(
                "Couldn't find the +18 warning modal, "
                "assuming there isn't one...")
        except Exception:
            self.logger.info("Nothing to download")

    def downloadChatLog(self):
        self.logger.info("Downloading chat log...")

        messages = self.driver.execute_async_script("""
            var done = arguments[0];
            window.indexedDB = window.indexedDB || window.mozIndexedDB || window.webkitIndexedDB || window.msIndexedDB;
            var db;
            var request = window.indexedDB.open("localforage", 2);
            request.onsuccess = function(event) {
                console.log(event);
                db = event.target.result;
                var transaction = db.transaction("keyvaluepairs", "readwrite");
                var objectStore = transaction.objectStore("keyvaluepairs");

                var test = objectStore.get("room:""" + self.room + """:messages");
                test.onsuccess = function(event) {
                    done(event.target.result);
                }
            };
        """)

        if not messages:
            self.logger.info("No chat log to download")
            return

        # Create necessary directories
        path = os.path.join(self.output_dir, self.room)
        path = os.path.join(path,
                            datetime.now().strftime(config.archive_date_format))
        if not os.path.exists(path):
            os.makedirs(path)
        path = os.path.join(path, "chat.log")

        # Get only the new messages
        new_messages = [x for x in messages if x not in self.old_chat_messages]
        self.old_chat_messages = messages

        for message in new_messages:
            owner = "♕" if "owner" in message["options"] else ""

            texts = []
            stop = False
            for m in message["message"]:
                text = str(m)

                if m["type"] == "text":
                    text = m["value"]
                elif m["type"] == "file":
                    text = "%s - %s (%s)" % (m["id"],
                                             m["name"],
                                             m["filetype"])
                elif m["type"] == "url":
                    text = "%s (%s)" % (m["text"], m["href"])

                if text in config.chat_messages_to_ignore:
                    stop = True
                    continue

                texts.append(text)

            if (message["nick"] in config.chat_nicks_to_ignore) or stop:
                continue

            with open(path, "a+", encoding="utf-8") as f:
                f.write("%s%s: %s\n" % (
                    owner,
                    message["nick"],
                    "\n".join(texts))
                )

        self.logger.info(
            "Downloaded chat log with %d new messages" % len(new_messages))

    def getFilesList(self):
        """Get the list of files from a room and prepare the information
        of each file
        """

        wait = ui.WebDriverWait(self.driver, 3)

        # Wait for the list of files and get them
        try:
            files = wait.until(lambda driver:
                               driver.find_elements_by_css_selector(
                                   "#file_list .filelist_file"))
        except TimeoutException:
            self.logger.error(
                "Couldn't find the list of files, aborting...")
            return (Result.ERROR, None)

        # Get all files information
        files_list_output = []
        for file_elem in files:

            file_left_part = file_elem.find_element_by_class_name(
                "file_left_part")

            file_right_part = file_elem.find_element_by_class_name(
                "file_right_part")

            url = file_left_part.get_attribute("href")

            file_tag = file_left_part.find_element_by_class_name(
                "file_tag").get_attribute("innerHTML")

            file_size_expiration = file_right_part.get_attribute("innerHTML")
            size_expiration_pattern = re.compile(r"^(.*?)<.*>(.*)<\/span>")
            size_expiration_info = size_expiration_pattern.findall(
                file_size_expiration)

            file_size = size_expiration_info[0][0]
            file_expiration = size_expiration_info[0][1]

            file_id, real_file_name = get_file_id_and_name(url)

            file_name_without_extension, extension = get_file_extension(
                real_file_name)

            files_list_output.append({
                "id": file_id,
                "url": url,
                "name": sanitize_file_name(file_name_without_extension),
                "extension": extension,
                "tag": file_tag,
                "size": humanfriendly.parse_size(file_size),
                "expiration": file_expiration
            })

        if config.download_oldest_first:
            files_list_output = files_list_output[::-1]

        return (Result.SUCCESS, files_list_output)

    def isPasswordNeeded(self):
        wait = ui.WebDriverWait(self.driver, 2)
        try:
            wait.until(
                lambda driver: driver.find_element_by_css_selector(
                    self.PASSWORD_INPUT_CSS_SELECTOR))
            return True
        except TimeoutException:
            return False

    def typePasswordIfNeeded(self, password):
        wait = ui.WebDriverWait(self.driver, 5)
        try:
            password_input = wait.until(
                lambda driver: driver.find_element_by_css_selector(
                    self.PASSWORD_INPUT_CSS_SELECTOR))

            if not password:
                self.logger.error("This room requires a password and you "
                                  "didn't type one")
                return

            password_input.send_keys(password)

            wait.until(
                lambda driver: driver.find_element_by_xpath(
                    self.PASSWORD_BUTTON_XPATH)
            ).click()
        except TimeoutException:
            self.logger.info("This room doesn't require a password")
            if password:
                self.logger.info("You typed a password for a room that "
                                 "doesn't require one")
            return True

        # Verify if is asking again for the password, if yes then the
        # password that we typed is wrong
        time.sleep(1)
        if self.isPasswordNeeded():
            self.logger.error("This room required a password but you "
                              "typed the wrong one")
            return False

        return True

    def closeDriver(self):
        """Close driver"""

        if self.driver:
            try:
                # self.driver.close()
                self.driver.quit()
            except Exception:
                self.logger.error("Something happened while trying to close "
                                  "the driver")

    def stop(self):
        self.looping = False
        self.closeDriver()
