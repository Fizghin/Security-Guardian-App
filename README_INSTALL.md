# AI Security Guardian - Installation Guide

This guide describes how to install and run the AI Security Guardian system on a new Windows computer.

## Prerequisites
Before you begin, ensure the following are installed:
1.  **Python 3.10 or newer** (Make sure to check "Add Python to PATH" during installation)
2.  **Node.js (LTS version)** (Required for the frontend)
3.  **Git** (Optional, for cloning)

## Installation
1.  Open the folder containing these files.
2.  Double-click `install.bat`.
    - This script will:
        - Create a Python virtual environment (`venv`).
        - Install all required Python libraries (including Facial Recognition models).
        - Install Node.js dependencies for the web interface.
    - *Note: This process may take 5-10 minutes depending on your internet speed.*

## Running the Application
1.  Double-click `start.bat`.
    - This will verify the environment and launch two windows:
        - **Backend Server**: The AI brain processing video feeds.
        - **Frontend Interface**: The web dashboard.
2.  Open your browser and navigate to: [http://localhost:2500](http://localhost:2500)

## Features
- **Facial Recognition**: To add "Insiders", go to System Config -> Insider Management.
- **Smart Detection**: automatically detects persons and escalates threat levels.

## Troubleshooting
- **Dependency Errors**: If `install.bat` fails on "deepface", ensure you have C++ Build Tools installed (needed for some Python packages) or try running `pip install "numpy<2.0" deepface tf-keras` manually in the virtual environment.
- **Port Conflicts**: Ensure ports `8002` (Backend) and `2500` (Frontend) are free.
