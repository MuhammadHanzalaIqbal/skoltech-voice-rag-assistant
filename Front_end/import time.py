import time
import random
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# Set up the WebDriver
options = webdriver.ChromeOptions()
options.add_argument("--start-maximized")  # Open browser in full screen
driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

# Open the target URL
url = "https://example.com"  # Replace with your URL
driver.get(url)

# Infinite loop to refresh the page at random intervals
try:
    while True:
        wait_time = random.randint(60, 300)  # Wait between 1 and 5 minutes
        print(f"Refreshing in {wait_time} seconds...")
        time.sleep(wait_time)
        driver.refresh()  # Refresh the page
        print("Page refreshed!")
except KeyboardInterrupt:
    print("Stopping the script...")
    driver.quit()  # Close the browser when interrupted
