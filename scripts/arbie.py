import requests
import pandas as pd
from pathlib import Path
from loguru import logger
import sys


PROJECT_DIR = Path(__file__).parent.parent
TOKENS_LIST_URL = "https://apiv4.paraswap.io/v2/tokens/1"

# Logger Setup
log_file = PROJECT_DIR.joinpath("logs/arbie.log")
log_format = "<g>{time}</> - <lvl>{level}</> - {message}"
logger.remove()
logger.add(sys.stdout, format=log_format)
logger.add(log_file, format=log_format, rotation="5 MB", compression="gz")

# Fetch the token list if it doesn't exist
tokens_fp = PROJECT_DIR.joinpath("data/tokens.csv")
if not tokens_fp.exists():
    tokens_fp.parent.mkdir(parents=True, exist_ok=True)
    tokens = requests.get(TOKENS_LIST_URL).json()["tokens"]
    tokens_df = pd.DataFrame.from_records(tokens, index="address")
    tokens_df.to_csv(tokens_fp)
    logger.debug("Fetched and saved token list from paraswap api")
else:
    tokens_df = pd.read_csv(tokens_fp, index_col="address")


def main():
    pass
