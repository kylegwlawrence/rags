#!/bin/bash

# Configuration
KAGGLE_USERNAME="your_kaggle_username"   # Change this
KAGGLE_API_KEY="your_kaggle_api_key"     # Change this
DOWNLOAD_DIR="~/data/kaggle"             # Change this to your preferred path

# Dataset to download — format: "owner/dataset-name"
# Examples:
#   "wikimedia/wikipedia"
#   "Cornell-University/arxiv"
#   "datasets/mnist"
DATASET="owner/dataset-name"            # Change this

# Optional: specific file within the dataset (leave empty to download all files)
FILE=""

# Setup
mkdir -p $(eval echo $DOWNLOAD_DIR)

# Install kaggle CLI if needed
pip3 install kaggle 2>/dev/null

# Write API credentials
mkdir -p ~/.kaggle
cat > ~/.kaggle/kaggle.json << CREDENTIALS
{"username":"$KAGGLE_USERNAME","key":"$KAGGLE_API_KEY"}
CREDENTIALS
chmod 600 ~/.kaggle/kaggle.json

# Download
if [ -z "$FILE" ]; then
    echo "Downloading full dataset: $DATASET"
    kaggle datasets download \
        --dataset "$DATASET" \
        --path $(eval echo $DOWNLOAD_DIR) \
        --unzip
else
    echo "Downloading file: $FILE from $DATASET"
    kaggle datasets download \
        --dataset "$DATASET" \
        --file "$FILE" \
        --path $(eval echo $DOWNLOAD_DIR) \
        --unzip
fi

echo "Done. Files saved to $(eval echo $DOWNLOAD_DIR)"
