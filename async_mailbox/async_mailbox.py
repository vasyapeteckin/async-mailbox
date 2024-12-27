import logging
import re
from email import message_from_bytes
from email.policy import default
from typing import List, Optional, Literal, Any
import aioimaplib


class AsyncMailbox:
    def __init__(self, host: str, email: str, password: str, logger=None):
        """
        Initialize the async_mailbox.

        :param host: The IMAP server hostname.
        :param email: The email address to authenticate with.
        :param password: The password for the email account.
        :param logger: An optional Logger instance for logging messages.
        """
        self.logger = logger
        self.host = host
        self.email = email
        self.password = password
        self.imap_client = None

        self.is_authenticated = False
        self.mailbox = None

        if self.logger is None:
            self.logger = logging.getLogger('AsyncMailbox')

    async def connect_and_authenticate(self) -> bool:
        """
        Connect to the IMAP server and authenticate with the provided credentials.

        :return: True if authentication is successful, False otherwise.
        """
        if not self.is_authenticated:
            self.imap_client = aioimaplib.IMAP4_SSL(host=self.host)
            await self.imap_client.wait_hello_from_server()

            res, _ = await self.imap_client.login(self.email, self.password)
            if res != 'OK':
                self.logger.error("Authentication failed.")
                return False
            self.logger.debug("Successfully authenticated.")
            self.is_authenticated = True
        return True

    async def select_mailbox(self, mailbox: str) -> bool:
        """
        Select a specific mailbox on the IMAP server.

        :param mailbox: The name of the mailbox to select.
        :return: True if the mailbox is selected successfully, False otherwise.
        """
        res, _ = await self.imap_client.select(mailbox)
        if res != 'OK':
            self.logger.error(f"Failed to select mailbox: {mailbox}")
            return False
        self.logger.debug(f"Mailbox {mailbox} selected.")
        self.mailbox = mailbox
        return True

    async def _search_all_emails(self) -> List[str]:
        """
        Search for all emails in the currently selected mailbox.

        :return: A list of message IDs for all emails found in the mailbox.
        """
        search_criteria = f'(ALL)'
        res, message_ids, *_ = await self.imap_client.search(search_criteria)
        if res != 'OK':
            self.logger.error("Failed to search mailbox.")
            return []

        message_ids = message_ids[0]
        if message_ids == b'':
            self.logger.warning("No messages found.")
            return []

        return message_ids.decode('utf-8').split(' ')

    async def delete_message(self, msg_id):
        # Mark the message as deleted
        await self.imap_client.store(msg_id, "+FLAGS", r"(\Deleted)")
        self.logger.debug(f"Message ID {msg_id} marked as deleted.")

        await self.imap_client.expunge()
        self.logger.debug(f"Message ID {msg_id} expunged.")

    async def _process_emails(self,
                              message_ids: List[str],
                              include_text: Optional[str] = "",
                              text_type: Literal['PLAIN', 'HTML'] = "HTML",
                              delete_after_read: bool = False) -> list[dict[str, int | None | list[Any] | Any]]:
        """
        Process emails by fetching their content and extracting URLs.

        :param message_ids: List of message IDs to fetch and process.
        :param include_text: Optional text that must be included in the email content.
        :param text_type: The type of content to fetch ('PLAIN' or 'HTML').
        :param delete_after_read: If True, mark messages as deleted after reading.
        :return: A list of dictionaries containing message details like ID, content, and URLs.
        """
        text_type_mapping = {"PLAIN": "text/plain", "HTML": "text/html"}
        messages = []
        for msg_id in message_ids[::-1]:  # Process the messages in reverse order
            res, msg_data = await self.imap_client.fetch(msg_id, '(RFC822)')
            if res != 'OK':
                self.logger.error(f"Failed to fetch message ID {msg_id}.")
                continue
            message = None
            email_message = message_from_bytes(msg_data[1], policy=default)
            for part in email_message.walk():
                if part.get_content_type() == text_type_mapping[text_type]:
                    content = part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8')
                    self.logger.debug(f"Message ID {msg_id} content: {content}")
                    if include_text == "" or include_text.lower() in content.lower():
                        message = {
                            "message_id": int(msg_id),
                            "content": content,
                            "urls": re.findall(r'https?://\S+', content),
                        }

                        if delete_after_read:
                            await self.delete_message(msg_id=msg_id)

            if message:
                messages.append(message)
            self.logger.debug(messages)
        return messages

    async def logout(self) -> None:
        """
        Log out and disconnect from the IMAP server.

        This ensures that any open session is closed and resources are freed.
        """
        if self.imap_client:
            await self.imap_client.logout()
            self.logger.debug("Logged out successfully.")
            self.is_authenticated = False

    async def check_mailbox(self,
                            mailbox: str = 'INBOX',
                            include_text: Optional[str] = "",
                            text_type: Literal['PLAIN', 'HTML'] = "HTML",
                            delete_after_read: bool = False) -> list[dict]:
        """
        Check a mailbox for emails, search for content, and extract URLs.

        This method is the main interface for users to check for emails. It searches for emails in the specified
        mailbox and processes them to extract URLs if they match the `include_text` filter.

        :param mailbox: The mailbox to select (default is 'INBOX').
        :param include_text: Text to search for in the email content. Only emails with this text will be processed.
        :param text_type: The type of content to search for ('PLAIN' or 'HTML').
        :param delete_after_read: If True, delete emails after processing them.
        :return: A list of dictionaries containing email information such as message ID, content, and extracted URLs.
        """
        if not await self.connect_and_authenticate():
            return []

        if not await self.select_mailbox(mailbox):
            await self.logout()
            return []

        message_ids = await self._search_all_emails()
        if not message_ids:
            await self.logout()
            return []

        messages = await self._process_emails(message_ids=message_ids,
                                              include_text=include_text,
                                              text_type=text_type,
                                              delete_after_read=delete_after_read)
        # await self.logout()
        return messages
