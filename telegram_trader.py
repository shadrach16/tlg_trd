import sys
from telethon import TelegramClient, events
from telethon.tl.types import User, Channel, Chat # Import types to check chat type
from loguru import logger
import asyncio
import re
import time
from datetime import datetime, timedelta
import pytz # For timezone conversions
import tzlocal # For automatically determining local timezone
from pocketoptionapi.stable_api import PocketOption
import pocketoptionapi.global_value as global_value 

logger.remove()
logger.add(sys.stderr, format="{time:YYYY-MM-DD HH:mm:ss} {level} {message}", level="INFO")
logger.add("telegram_pocketoption_trades.log", rotation="500 MB", level="INFO") # Log to a file

# --- PocketOption Configuration ---
POCKETOPTION_SSID = r'42["auth",{"session":"u6mmgu09dbppruclouvoo9nlo5","isDemo":1,"uid":104690528,"platform":2,"isFastHistory":true}]'
IS_POCKETOPTION_DEMO = True  
NEW_YORK_TZ = pytz.timezone('America/New_York')
INITIAL_BALANCE = 51232.93 # Example: Starting with $1000.00


# --- Telegram Configuration ---
API_ID = 23329526 # Replace with your actual API ID
API_HASH = '52295843fb569259d1ac199afc868f0b' # Replace with your actual API Hash
SESSION_NAME = 'my_telegram_user_session'
PHONE_NUMBER = '+2349158929344'  

try:
    SYSTEM_TZ = tzlocal.get_localzone()
    logger.info(f"Automatically determined system timezone: {SYSTEM_TZ}")
except Exception as e:
    logger.error(f"Could not automatically determine system timezone: {e}. Falling back to 'Africa/Lagos'.")
    SYSTEM_TZ = pytz.timezone('Africa/Lagos') # Fallback to a default if auto-detection fails





# --- PocketOption Client Wrapper (for synchronous API in async environment) ---
class PocketOptionClient:
    def __init__(self, ssid, is_demo, initial_balance):
        self.ssid = ssid
        self.is_demo = is_demo
        self.internal_balance = float(initial_balance) # Managed internally
        
        try:
            self._po_client = PocketOption(self.ssid, demo=self.is_demo)
            logger.debug("PocketOption client object initialized.")
        except Exception as e:
            logger.critical(f"Failed to instantiate PocketOption client: {e}. Check pocketoptionapi library version.", exc_info=True)
            self._po_client = None
        self.is_connected = False

    def _sync_connect(self, global_val_module):
        """Synchronous connect method for PocketOption."""
        if not self._po_client:
            logger.error("PocketOption client not initialized. Cannot connect.")
            return False

        logger.debug("Calling self._po_client.connect() to start WebSocket thread.")
        check_connect = self._po_client.connect() # This initiates the connection in a separate thread
        logger.info(f"PocketOption raw connect() result: {check_connect}") # Added for debugging

        if check_connect:
            timeout = 30 # seconds for overall connection and balance update
            start_time = time.time()
            
            logger.debug(f"Waiting for WebSocket connection (timeout: {timeout}s)...")
            while not global_val_module.websocket_is_connected and (time.time() - start_time < timeout):
                logger.debug(f"  WebSocket status: {global_val_module.websocket_is_connected}. Waiting...")
                time.sleep(0.5)
            
            if not global_val_module.websocket_is_connected:
                logger.error(f"PocketOption: WebSocket connection timed out after {time.time() - start_time:.2f}s. State: {global_val_module.websocket_is_connected}.")
                self.is_connected = False
                return False
            logger.debug("WebSocket connection established.")

            # We are not relying on API's get_balance for the current balance,
            # but we still want to ensure the API receives initial account data.
            # The global_value.balance_updated flag is still useful for confirming readiness.
            start_time = time.time() # Reset timer for balance update
            logger.debug(f"Waiting for initial balance update (timeout: {timeout}s)...")
            while not global_val_module.balance_updated and (time.time() - start_time < timeout):
                logger.debug(f"  Balance updated status: {global_val_module.balance_updated}. Waiting...")
                time.sleep(0.5)
            
            if not global_val_module.balance_updated:
                logger.warning(f"PocketOption: Balance update flag timed out after {time.time() - start_time:.2f}s. This might indicate an issue with initial account data reception, but we'll proceed with internal balance management. State: {global_val_module.balance_updated}.")
            else:
                logger.debug("Initial balance update flag received.")

            logger.success(f"PocketOption: Successfully connected. Managing balance internally: ${self.internal_balance:.2f} ({'Demo' if self.is_demo else 'Real'}).")
            self.is_connected = True
        else:
            logger.error(f"PocketOption: Failed to initiate connection. Check SSID and internet connection.")
            self.is_connected = False
        return self.is_connected

    async def connect(self):
        """Asynchronously calls the synchronous connect method."""
        return await asyncio.to_thread(self._sync_connect, global_value)

    def _sync_disconnect(self):
        """Synchronous disconnect method for PocketOption."""
        if self._po_client and self.is_connected:
            # Use disconnect() if close() is not available or causes issues
            self._po_client.disconnect() # Assuming disconnect() is the correct method based on API doc snippet
            logger.info("PocketOption: Disconnected.")
            self.is_connected = False

    async def disconnect(self):
        """Asynchronously calls the synchronous disconnect method."""
        await asyncio.to_thread(self._sync_disconnect)

    # Removed _sync_get_balance and get_account_balance as balance is managed internally
    # Keeping a placeholder method for consistency, but it will return the internal balance
    async def get_account_balance(self):
        """Returns the internally managed account balance."""
        return self.internal_balance


    def _sync_place_trade(self, symbol, action, amount, duration_seconds):
        """Synchronous trade placement method."""
        if not self._po_client or not self.is_connected:
            logger.warning("PocketOption: Not connected. Cannot place trade.")
            return False

        # Check internal balance before placing trade
        if amount > self.internal_balance:
            logger.error(f"Insufficient internal balance (${self.internal_balance:.2f}) to place trade of ${amount:.2f}.")
            return False

        logger.info(f"\n--- PLACING TRADE ---")
        logger.info(f"  Symbol: {symbol}, Action: {action}, Amount: ${amount:.2f}, Duration: {duration_seconds}s")
        logger.info(f"  Internal Balance Before Trade: ${self.internal_balance:.2f}")

        try:
            # Correct order of arguments for buy: amount, active, action, expirations
            buy_success, trade_id = self._po_client.buy(amount, symbol, action, duration_seconds)
            
            if not buy_success or trade_id is None:
                logger.error(f"Failed to place trade or no trade ID received. Buy success: {buy_success}, Trade ID: {trade_id}")
                return False

            logger.info(f"Trade placed. ID: {trade_id}. Waiting {duration_seconds}s for result...")

            # Wait for the trade to expire
            time.sleep(duration_seconds + 3) # Add buffer time

            profit_amount, status_string = self._po_client.check_win(trade_id)
            
            is_win = False
            if status_string == 'win':
                is_win = True
                self.internal_balance += profit_amount # Update internal balance on win
                logger.success(f"  Result: WIN! Profit: ${profit_amount:.2f}")
            elif status_string == 'loose': # Note: 'loose' spelling from the raw code
                self.internal_balance -= amount # Update internal balance on loss
                logger.error(f"  Result: LOSS! Amount lost: ${amount:.2f}")
            elif status_string == 'unknown':
                logger.warning(f"  Trade result is 'unknown'. No balance change for Martingale progression assumes loss.")
                # No change to internal_balance here as it implies a loss for Martingale progression
            else:
                logger.warning(f"  Unexpected trade result status: {status_string}. Profit: {profit_amount}. No balance change for Martingale progression assumes loss.")
                # No change to internal_balance here

            logger.info(f"  Internal Balance After Trade: ${self.internal_balance:.2f}")
            logger.info("-------------------------\n")
            return is_win

        except Exception as e:
            logger.error(f"Error placing trade on PocketOption: {e}", exc_info=True)
            return False

    async def place_trade(self, symbol, action, amount, duration_minutes):
        """Asynchronously calls the synchronous place trade method."""
        return await asyncio.to_thread(self._sync_place_trade, symbol, action, amount, duration_minutes * 60)

# --- Message Parsing ---
def parse_trade_message(message_text):
    """
    Parses a Telegram message to extract trade details based on the new sample.
    """
    signal = {}

    # Regex to extract Pair and OTC status (handling bold and emojis)
    # Sample: **🇬🇧 GBP/USD �🇸 OTC**
    pair_match = re.search(r'\*\*.*? ([\w\/]+?) .*? (OTC)\*\*', message_text)
    if pair_match:
        raw_pair = pair_match.group(1).strip().replace("/", "") # e.g., GBPUSD
        otc_flag = pair_match.group(2) # "OTC"
        
        # PocketOption stable_api sometimes uses '_otc' suffix for OTC pairs
        if otc_flag and otc_flag.upper() == "OTC":
            signal['pair'] = raw_pair + "_otc" # e.g., GBPUSD_otc
        else:
            signal['pair'] = raw_pair
    else:
        logger.warning("Could not parse Pair from message.")
        return None

    # Regex to extract Expiration (handling bold)
    # Sample: 🕘 Expiration **5M**
    # Re-parsing expiration from text, as hardcoding 5 was temporary
    exp_match = re.search(r'Expiration \*\*(\d+)M\*\*', message_text)
    if exp_match:
        signal['expiration_minutes'] = int(exp_match.group(1))
    else:
        logger.warning("Could not parse Expiration from message. Defaulting to 5 minutes.")
        signal['expiration_minutes'] = 5 # Fallback to a default if not found
        # return None # Or return None if expiration is strictly required

    # Regex to extract Entry Action (SELL/BUY) (handling bold and emoji)
    # Sample: 🟩 **BUY** or 🟥 **SELL**
    action_match = re.search(r'(🟩|🟥) \*\*(BUY|SELL)\*\*', message_text)
    if action_match:
        # Convert to 'put'/'call' as commonly used by trading APIs
        if "SELL" in action_match.group(2).upper():
            signal['action'] = "put"
        else:
            signal['action'] = "call"
    else:
        logger.warning("Could not parse Action (BUY/SELL) from message.")
        return None

    # Extract Entry Times (Initial and Martingale Levels)
    entry_times = []
    # Initial Entry
    # Sample: ⏺ Entry at **07:50**
    initial_entry_match = re.search(r'⏺ Entry at \*\*(\d{2}:\d{2})\*\*', message_text)
    if initial_entry_match:
        entry_times.append({'time_str': initial_entry_match.group(1), 'multiplier': 1.0, 'level': 0})
    else:
        logger.warning("Could not parse initial Entry Time from message.")
        return None

    # Martingale Levels
    # Sample: 1️⃣ level at 07:55
    martingale_matches = re.findall(r'(\d+)️⃣ level at (\d{2}:\d{2})', message_text)
    for match in martingale_matches:
        level = int(match[0])
        time_str = match[1]
        multiplier = 2.0 ** level # 1st level (2^1), 2nd level (2^2), 3rd level (2^3)
        entry_times.append({'time_str': time_str, 'multiplier': multiplier, 'level': level})

    # Sort entry times by their actual time
    signal['entry_sequence'] = sorted(entry_times, key=lambda x: datetime.strptime(x['time_str'], '%H:%M').time())

    return signal

def convert_ny_to_system_time(ny_time_str, system_tz):
    """
    Converts a time string from New York timezone to the system's local timezone.
    Assumes the time string is for the current or next day.
    """
    now_ny = datetime.now(NEW_YORK_TZ)
    # Combine today's date in NY with the target time
    ny_time_obj = datetime.strptime(ny_time_str, '%H:%M').time()
    target_ny_datetime = NEW_YORK_TZ.localize(datetime.combine(now_ny.date(), ny_time_obj))

    # If the target time has already passed today in NY, assume it's for the next day
    if target_ny_datetime < now_ny:
        target_ny_datetime += timedelta(days=1)

    # Convert to system timezone
    system_datetime = target_ny_datetime.astimezone(system_tz)
    return system_datetime

# --- Trading Logic ---
async def execute_trade_sequence(po_client, signal_data):
    """
    Executes the sequence of trades (initial + Martingale) based on signal.
    """
    pair = str(signal_data['pair'])
    action = signal_data['action']
    expiration_minutes = signal_data['expiration_minutes']
    entry_sequence = signal_data['entry_sequence']

    # Use internal balance here
    current_balance = po_client.internal_balance 
    if current_balance <= 0:
        logger.error("Error: Account balance is zero or negative. Cannot place trades.")
        return

    initial_trade_amount = current_balance * 0.01 # 1% of balance

    for i, trade_info in enumerate(entry_sequence):
        logger.info(str(trade_info))
        level = trade_info['level']
        ny_time_str = trade_info['time_str']
        multiplier = trade_info['multiplier']

        current_trade_amount = initial_trade_amount * multiplier
        current_trade_amount = round(current_trade_amount, 2) # Round to 2 decimal places

        # PocketOption minimum trade amount is often $1 or higher, ensure this is respected
        MIN_TRADE_AMOUNT = 1.0 # Assuming $1, adjust if PocketOption has a different minimum
        if current_trade_amount < MIN_TRADE_AMOUNT: 
            logger.warning(f"⚠️ Trade amount ${current_trade_amount:.2f} is below minimum trade amount (${MIN_TRADE_AMOUNT}). Adjusting to ${MIN_TRADE_AMOUNT}.")
            current_trade_amount = MIN_TRADE_AMOUNT

        # Re-check internal balance before each trade
        if current_trade_amount > po_client.internal_balance: # Check against internal balance
            logger.warning(f"⚠️ Insufficient internal balance for Level {level} trade. Required: ${current_trade_amount:.2f}, Available: ${po_client.internal_balance:.2f}. Stopping sequence.")
            break

        # Convert NY entry time to system local time
        entry_datetime_system = convert_ny_to_system_time(ny_time_str, SYSTEM_TZ)

        logger.info(f"\n--- Preparing for Trade Level {level} (Martingale {level if level > 0 else 'Initial'}) ---")
        logger.info(f"  Pair: {pair}, Action: {action}")
        if level == 0:
            logger.info(f"  Target NY Entry Time: {ny_time_str}")
            logger.info(f"  Converted System Entry Time: {entry_datetime_system.strftime('%Y-%m-%d %H:%M:%S %Z%z')}")
        logger.info(f"  Trade Amount: ${current_trade_amount:.2f}")

        
        if level == 0:
        # Wait until the entry time is reached
            now_system = datetime.now(SYSTEM_TZ)
            time_to_wait_seconds = (entry_datetime_system - now_system).total_seconds()

            if time_to_wait_seconds > 0:
                logger.info(f"  Waiting {int(time_to_wait_seconds)} seconds until entry time...")
                await asyncio.sleep(time_to_wait_seconds) # Use asyncio.sleep for async waiting
                logger.info("  Entry time reached. Executing trade.")
            else:
                logger.warning(f"  Entry time already passed by {abs(int(time_to_wait_seconds))} seconds. Executing trade immediately (consider skipping if too late).")
        else:
            logger.info(" Executing trade.")


        # Execute the trade
        trade_result_win = await po_client.place_trade(pair, action, current_trade_amount, expiration_minutes)

        if trade_result_win:
            logger.success(f"✅ Trade Level {level} resulted in a WIN! Stopping Martingale sequence.")
            break # Stop if trade is a win
        else:
            logger.error(f"❌ Trade Level {level} resulted in a LOSS.")
            if i < len(entry_sequence) - 1:
                logger.info(f"  Proceeding to next Martingale level {level + 1}.")
            else:
                logger.critical("  Maximum Martingale levels reached (Level 3 loss). Stopping sequence. Waiting for a new signal.")
                
    logger.info("\n--- Trade sequence finished ---")




# --- Main Logic ---
async def main():
    logger.info("Starting Telegram User Account Reader...")

    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    po_client = PocketOptionClient(POCKETOPTION_SSID, IS_POCKETOPTION_DEMO, INITIAL_BALANCE)

    try:
        logger.info("Connecting to Telegram as user...")
        await client.start(phone=PHONE_NUMBER)
        logger.success("Successfully connected to Telegram user account.")

        logger.info("Connecting to PocketOption...")
        if not await po_client.connect():
            logger.critical("Failed to connect to PocketOption. Exiting.")
            sys.exit(1)
        

        # --- Event Handler for New Messages ---
        @client.on(events.NewMessage)
        async def handler(event):
            # event.message contains the incoming message object
            message = event.message
            
            # Extract chat information
            chat = await message.get_chat()
            chat_id = chat.id
            chat_title = ""
            chat_type = "Unknown"


            if isinstance(chat, User):
                chat_title = f"{chat.first_name} (@{chat.username})" if chat.username else chat.first_name
                chat_type = "Private Chat"
            elif isinstance(chat, Channel):
                chat_title = chat.title
                chat_type = "Channel"
            elif isinstance(chat, Chat): # Basic group
                chat_title = chat.title
                chat_type = "Group"

            if 'youseff' in str(chat_title).lower():            
                # Log all incoming messages
                logger.info(f"\n--- NEW MESSAGE ---")
                logger.info(f"  Chat Type: {chat_type}")
                logger.info(f"  Chat ID: {chat_id}")
                logger.info(f"  Chat Name: {chat_title}")
                logger.info(f"  Date: {message.date.strftime('%Y-%m-%d %H:%M:%S')}")
                
                if message.text and 'Martingale levels' in message.text:
                    logger.info(f"\n--- Processing provided signal: ---\n{message.text}\n----------------------------------")

                    try:

                        signal = parse_trade_message(message.text)
                        
                        if signal:
                            logger.success("Trade signal parsed successfully. Initiating trade sequence.")
                            await execute_trade_sequence(po_client, signal)
                        else:
                            logger.error("Provided signal text did not contain a valid trade signal. No trades will be executed.")

                    except Exception as e:
                        logger.critical(f"An error occurred during trade execution: {e}", exc_info=True)
                    # finally:
                    #     await po_client.disconnect() # Ensure PocketOption client is disconnected

                else:
                    logger.info("  Content: Non-text message (e.g., photo, sticker, voice message).")
                logger.info("-------------------\n")

        logger.info("Listening for new messages. Press Ctrl+C to stop.")
        # Keep the client running indefinitely
        await client.run_until_disconnected()

    except Exception as e:
        logger.critical(f"An error occurred in the user bot: {e}", exc_info=True)
    finally:
        if client.is_connected():
            logger.info("Disconnecting Telegram user client.")
            await client.disconnect()

if __name__ == "__main__":
    logger.remove() 
    logger.add(sys.stderr, format="{time:YYYY-MM-DD HH:mm:ss} {level} {message}", level="INFO")
    logger.add("telegram_pocketoption_trades.log", rotation="500 MB", level="INFO")
    
    # Run the async main function
    asyncio.run(main())
