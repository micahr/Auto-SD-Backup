# Project Overview

This project, named SnapSync, is a Python-based service designed to automate the backup of photos and videos from SD cards. It is targeted at photographers who want a reliable and automated way to back up their work. The service detects when an SD card is inserted, and then backs up the files to both an [Immich](https://immich.app/) server and an Unraid (or any SMB/NFS) share. It also integrates with Home Assistant for status monitoring.

The application is built using a modern Python stack, including:

- **`asyncio`** for concurrent operations, which is ideal for I/O-bound tasks like file transfers.
- **`FastAPI`** to provide a web-based dashboard for monitoring and managing backups.
- **`click`** to create a user-friendly command-line interface.
- **`SQLAlchemy`** (inferred from the use of a SQLite database) for database interactions, to keep track of backed up files and prevent duplicates.
- **`pyudev`** for detecting SD card insertion on Linux.
- **`paho-mqtt`** for integrating with Home Assistant via the MQTT protocol.

The project is well-structured, with a clear separation of concerns between the different components. The main components are:

- **SD Card Detector**: Monitors for the insertion and removal of SD cards.
- **Backup Engine**: The core component that handles the backup process, including scanning for files, calculating checksums, and transferring files to the backup destinations.
- **Immich Client**: A client for interacting with the Immich API.
- **Unraid Client**: A client for interacting with Unraid or other SMB/NFS shares.
- **MQTT Client**: A client for publishing status updates to a Home Assistant instance.
- **Web UI**: A web-based dashboard for monitoring and managing backups.
- **CLI**: A command-line interface for interacting with the service.

# Building and Running

The project includes a `requirements.txt` file, which lists all the Python dependencies. These can be installed using `pip`.

## Installation

The recommended way to install the project is to use the provided `install.sh` script:

```bash
sudo ./install.sh
```

This will install system dependencies, create a Python virtual environment, install the required Python packages, and set up a systemd service to run the application in the background.

## Running the Service

Once installed, the service can be managed using `systemd`:

- **Start the service:** `sudo systemctl start snapsync`
- **Stop the service:** `sudo systemctl stop snapsync`
- **Check the status of the service:** `sudo systemctl status snapsync`
- **View the service logs:** `sudo journalctl -u snapsync -f`

The service can also be run manually in the foreground for development and debugging purposes:

```bash
snapsync start
```

## Running Tests

The project includes a `requirements-test.txt` file, which suggests that there is a test suite. The `pytest.ini` file indicates that the tests are run using the `pytest` framework. To run the tests, you would typically install the test dependencies and then run `pytest`:

```bash
pip install -r requirements-test.txt
pytest
```

# Development Conventions

The project follows standard Python development conventions. The code is well-structured and organized into modules with clear responsibilities. The use of `asyncio` suggests a modern, asynchronous programming style. The project also includes a `DEVELOPMENT.md` file, which likely contains more detailed information about the development process.

The project uses a `config.yaml` file for configuration, with a `config.yaml.example` file provided as a template. Sensitive information, such as API keys and passwords, is stored in a `.env` file, which is a good security practice.

The code is formatted using a consistent style, although there is no explicit mention of a specific code formatter (like Black or YAPF). The use of type hints is also a good practice that improves code readability and maintainability.
