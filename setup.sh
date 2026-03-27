#!/bin/bash
# One-command setup for the auto-trader on a fresh Ubuntu 24.04 droplet.
# Run as the deploy user (not root): bash setup.sh
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "Setting up auto-trader in: $PROJECT_DIR"

# ------------------------------------------------------------------
# 1. System packages
# ------------------------------------------------------------------
sudo apt-get update -y
sudo apt-get install -y python3.12 python3.12-venv python3-pip git curl

# ------------------------------------------------------------------
# 2. 1GB swap file (memory safety buffer for 1GB droplet)
# ------------------------------------------------------------------
if [ ! -f /swapfile ]; then
    echo "Creating 1GB swap file..."
    sudo fallocate -l 1G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    echo "Swap file created and enabled."
else
    echo "Swap file already exists — skipping."
fi

# ------------------------------------------------------------------
# 3. Python virtual environment
# ------------------------------------------------------------------
if [ ! -d "$PROJECT_DIR/venv" ]; then
    python3.12 -m venv "$PROJECT_DIR/venv"
    echo "Virtual environment created."
fi

source "$PROJECT_DIR/venv/bin/activate"
pip install --upgrade pip
pip install -r "$PROJECT_DIR/requirements.txt"
echo "Dependencies installed."

# ------------------------------------------------------------------
# 4. Create required directories (logs, cache)
# ------------------------------------------------------------------
mkdir -p "$PROJECT_DIR/logs"
mkdir -p "$PROJECT_DIR/data/cache"
mkdir -p "$PROJECT_DIR/data/cache/ta"

# ------------------------------------------------------------------
# 5. .env file
# ------------------------------------------------------------------
if [ ! -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    echo ""
    echo "IMPORTANT: $PROJECT_DIR/.env has been created from .env.example."
    echo "Fill in all API keys before running the pipeline."
    echo ""
fi

# ------------------------------------------------------------------
# 6. Make scripts executable
# ------------------------------------------------------------------
chmod +x "$PROJECT_DIR/cron/run_daily.sh"

# ------------------------------------------------------------------
# 7. Cron setup (print instructions — don't auto-add to avoid duplicates)
# ------------------------------------------------------------------
echo ""
echo "=== CRON SETUP ==="
echo "Add these two lines to your crontab (crontab -e):"
echo ""
echo "# EDT (Mar–Nov): 4:30pm ET = 20:30 UTC"
echo "30 20 * * 1-5 $PROJECT_DIR/cron/run_daily.sh >> $PROJECT_DIR/logs/cron.log 2>&1"
echo ""
echo "# EST (Nov–Mar): 4:30pm ET = 21:30 UTC"
echo "30 21 * * 1-5 $PROJECT_DIR/cron/run_daily.sh >> $PROJECT_DIR/logs/cron.log 2>&1"
echo ""
echo "=== SETUP COMPLETE ==="
echo "Next steps:"
echo "  1. Fill in $PROJECT_DIR/.env with your API keys"
echo "  2. Fill in email_to / email_from in config.yaml"
echo "  3. Add the cron entries above (crontab -e)"
echo "  4. Test: source venv/bin/activate && python -m src.main"
