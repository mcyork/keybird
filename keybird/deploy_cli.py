#!/usr/bin/env python3
"""
Keybird deployment CLI - Deploy Keybird to a Raspberry Pi remotely
"""

import sys
import subprocess
import os
import shutil
from importlib import resources
from pathlib import Path


def check_pi_shell():
    """Check if pi-shell is installed"""
    return shutil.which('pi-shell') is not None


def check_pi_exists(pi_name):
    """Check if Pi is configured in pi-shell"""
    try:
        result = subprocess.run(['pi-shell', 'status', pi_name],
                               capture_output=True, text=True)
        return result.returncode == 0
    except Exception:
        return False


def get_package_file(filename):
    """Get path to a file in the package, preferring source files over installed package"""
    # Strategy: Prefer source files for development workflow
    # 1. Check script's directory (works when running from source)
    script_dir = Path(__file__).parent.absolute()
    source_path = script_dir / filename
    if source_path.exists():
        return str(source_path)
    
    # 2. Check current working directory for source files (for development)
    # This handles the case where keybird-deploy is installed but we're in the project directory
    cwd = Path.cwd()
    # Look for keybird/ subdirectory in current directory or parent
    for search_dir in [cwd, cwd.parent]:
        keybird_dir = search_dir / 'keybird'
        source_path = keybird_dir / filename
        if source_path.exists():
            return str(source_path)
    
    # 3. Fallback: use installed package files
    try:
        # Use importlib.resources for modern Python 3.10+
        if hasattr(resources, 'files'):
            # Python 3.9+ - use the modern API
            return str(resources.files('keybird') / filename)
        else:
            # Fallback for older versions (though we require 3.10+)
            return str(resources.path('keybird', filename))
    except Exception as e:
        print(f"❌ Error: Could not find package file '{filename}'")
        print(f"   Tried source paths: {script_dir / filename}, {cwd / 'keybird' / filename}")
        print(f"   Resource error: {e}")
        sys.exit(1)


def deploy_to_pi(pi_name):
    """Deploy Keybird to the specified Pi"""
    
    REMOTE_PATH = "/home/pi/pi-hid-bridge"
    
    print(f"🚀 Deploying Keybird to '{pi_name}'...")
    print()
    
    # Check if Pi is online
    print("📡 Checking if Pi is online...")
    result = subprocess.run(
        [pi_name, 'run', 'echo Connected'],
        capture_output=True, text=True
    )
    
    if result.returncode != 0:
        print(f"❌ Error: Cannot connect to '{pi_name}'")
        print("   Check that the Pi is online with: pi-shell status")
        return False
    
    # Get Pi info
    print("🔍 Detecting Pi model...")
    result = subprocess.run(
        [pi_name, 'run-stream', "cat /proc/device-tree/model 2>/dev/null || echo 'Unknown'"],
        capture_output=True, text=True
    )
    pi_model = result.stdout.strip() if result.returncode == 0 else "Unknown"
    
    result = subprocess.run(
        [pi_name, 'run-stream', "hostname -I | awk '{print $1}'"],
        capture_output=True, text=True
    )
    pi_ip = result.stdout.strip() if result.returncode == 0 else "Unknown"
    
    print(f"   Model: {pi_model}")
    print(f"   IP: {pi_ip}")
    print()
    
    # Step 1: Create directory structure
    print("📁 Creating directory structure...")
    result = subprocess.run(
        [pi_name, 'run-stream', f"mkdir -p {REMOTE_PATH}/{{scripts,systemd,templates,static/css,static/js}}"],
        text=True
    )
    
    if result.returncode != 0:
        print("❌ Failed to create directories")
        return False
    
    # Step 2: Upload files
    print("📤 Uploading application files...")
    
    files_to_send = [
        ('server.py', f'{REMOTE_PATH}/pi_kb.py'),
        ('templates/index.html', f'{REMOTE_PATH}/templates/index.html'),
        ('static/css/style.css', f'{REMOTE_PATH}/static/css/style.css'),
        ('static/js/app.js', f'{REMOTE_PATH}/static/js/app.js'),
        ('scripts/setup_gadget_composite.sh', f'{REMOTE_PATH}/scripts/setup_gadget_composite.sh'),
        ('scripts/cleanup_gadget.sh', f'{REMOTE_PATH}/scripts/cleanup_gadget.sh'),
        ('scripts/gadget.conf', f'{REMOTE_PATH}/scripts/gadget.conf'),
        ('systemd/hid-gadget.service', f'{REMOTE_PATH}/systemd/hid-gadget.service'),
        ('systemd/pi-hid-bridge.service', f'{REMOTE_PATH}/systemd/pi-hid-bridge.service'),
    ]
    
    for local_file, remote_file in files_to_send:
        local_path = get_package_file(local_file)
        result = subprocess.run(
            [pi_name, 'send', local_path, remote_file],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"❌ Failed to upload {local_file}")
            return False
    
    # Step 3: Set permissions
    print("🔧 Setting permissions...")
    result = subprocess.run(
        [pi_name, 'run-stream', f'chmod +x {REMOTE_PATH}/scripts/*.sh'],
        text=True
    )
    
    # Step 4: Install system packages (pip3 and python3-evdev)
    print("📦 Installing system packages...")
    print("   This may take a minute on first install...")

    # Refresh the apt index first. A stale index points at package versions the
    # mirror no longer has, so the install below fails with 404 "Failed to fetch"
    # errors. This error condition is eaten by the current code.
    # Run update on its own so a failure here is visible, not masked.
    print("   Refreshing package index (apt-get update)...")
    update_result = subprocess.run(
        [pi_name, 'run-stream', 'sudo apt-get update'],
        text=True
    )
    if update_result.returncode != 0:
        print("⚠️  Warning: apt-get update failed - the package install may 404")

    result = subprocess.run(
        [pi_name, 'run-stream', 'sudo apt-get install -y python3-pip python3-flask python3-evdev 2>&1 | tail -10'],
        text=True
    )
    
    if result.returncode != 0:
        print("⚠️  Warning: Failed to install system packages")
        print("   Trying pip as fallback...")
        # Fallback: try pip if it exists
        subprocess.run(
            [pi_name, 'run-stream', 'sudo pip3 install flask evdev --break-system-packages 2>&1 | tail -5'],
            text=True
        )
    
    # Step 5: Configure boot config for USB gadget mode
    print()
    print("⚙️  Configuring USB gadget mode in boot config...")
    
    # Backup config
    subprocess.run(
        [pi_name, 'run-stream', 'sudo cp /boot/firmware/config.txt /boot/firmware/config.txt.backup 2>/dev/null || sudo cp /boot/config.txt /boot/config.txt.backup || true'],
        capture_output=True
    )
    
    # Check and configure dwc2 overlay with FIFO parameters (critical for Pi 4)
    DWC2_CONFIG = "dtoverlay=dwc2,dr_mode=peripheral,g-rx-fifo-size=256,g-np-tx-fifo-size=32"
    
    result = subprocess.run(
        [pi_name, 'run-stream', "grep 'dtoverlay=dwc2' /boot/firmware/config.txt 2>/dev/null || grep 'dtoverlay=dwc2' /boot/config.txt 2>/dev/null"],
        capture_output=True, text=True
    )
    
    if result.returncode == 0:
        # dwc2 exists - check if it's set correctly
        if 'dr_mode=peripheral' in result.stdout and 'g-rx-fifo-size' in result.stdout:
            print("   ✓ dwc2 overlay already configured correctly")
        else:
            print("   Fixing dwc2 overlay (adding peripheral mode + FIFO parameters)...")
            # Remove old dtoverlay=dwc2 lines and add correct one under [all]
            subprocess.run(
                [pi_name, 'run-stream', f"sudo sed -i '/dtoverlay=dwc2/d' /boot/firmware/config.txt 2>/dev/null || sudo sed -i '/dtoverlay=dwc2/d' /boot/config.txt"],
                text=True
            )
            subprocess.run(
                [pi_name, 'run-stream', f"echo '{DWC2_CONFIG}' | sudo tee -a /boot/firmware/config.txt >/dev/null 2>&1 || echo '{DWC2_CONFIG}' | sudo tee -a /boot/config.txt >/dev/null"],
                text=True
            )
    else:
        print("   Adding dwc2 overlay to config.txt (under [all] section)...")
        subprocess.run(
            [pi_name, 'run-stream', f"echo '{DWC2_CONFIG}' | sudo tee -a /boot/firmware/config.txt >/dev/null 2>&1 || echo '{DWC2_CONFIG}' | sudo tee -a /boot/config.txt >/dev/null"],
            text=True
        )
    
    # Check and add modules-load
    result = subprocess.run(
        [pi_name, 'run-stream', "grep -q 'modules-load=dwc2' /boot/firmware/cmdline.txt 2>/dev/null || grep -q 'modules-load=dwc2' /boot/cmdline.txt 2>/dev/null"],
        capture_output=True
    )
    
    if result.returncode == 0:
        print("   ✓ modules-load already configured")
    else:
        print("   Adding modules-load to cmdline.txt...")
        subprocess.run(
            [pi_name, 'run-stream', "sudo sed -i 's/rootwait/rootwait modules-load=dwc2,g_hid/' /boot/firmware/cmdline.txt 2>/dev/null || sudo sed -i 's/rootwait/rootwait modules-load=dwc2,g_hid/' /boot/cmdline.txt"],
            text=True
        )
    
    # Step 6: Install systemd services
    print()
    print("🔧 Installing systemd services...")
    subprocess.run(
        [pi_name, 'run-stream', f'sudo cp {REMOTE_PATH}/systemd/hid-gadget.service /etc/systemd/system/'],
        text=True
    )
    subprocess.run(
        [pi_name, 'run-stream', f'sudo cp {REMOTE_PATH}/systemd/pi-hid-bridge.service /etc/systemd/system/'],
        text=True
    )
    subprocess.run(
        [pi_name, 'run-stream', 'sudo systemctl daemon-reload'],
        text=True
    )
    subprocess.run(
        [pi_name, 'run-stream', 'sudo systemctl enable hid-gadget.service'],
        text=True
    )
    subprocess.run(
        [pi_name, 'run-stream', 'sudo systemctl enable pi-hid-bridge.service'],
        text=True
    )
    
    print()
    print("✅ Deployment complete!")
    print()
    print("📋 What was deployed:")
    print("   ✅ Composite HID gadget (Keyboard + Media Keys + Mouse)")
    print("   ✅ Flask web app with all features")
    print("   ✅ Systemd auto-start services")
    print("   ✅ Boot configuration for USB gadget mode")
    print()
    print("📋 Next steps:")
    print(f"   1. Reboot the Pi:")
    print(f"      {pi_name} run-stream 'sudo reboot'")
    print()
    print(f"   2. Wait ~30-45 seconds for reboot")
    print()
    print(f"   3. Services will auto-start! Check status:")
    print(f"      {pi_name} run-stream 'sudo systemctl status hid-gadget.service'")
    print(f"      {pi_name} run-stream 'sudo systemctl status pi-hid-bridge.service'")
    print()
    print(f"   4. Open web UI:")
    print(f"      http://{pi_ip}:8080")
    print()
    
    # Prompt for reboot
    try:
        response = input("Reboot now? (y/N): ").strip().lower()
        if response == 'y':
            print(f"🔄 Rebooting {pi_name}...")
            subprocess.run([pi_name, 'run-stream', 'sudo reboot'])
            print("✅ Reboot initiated. Wait 30 seconds before accessing web UI.")
            return True
    except KeyboardInterrupt:
        print()
        print("Skipped reboot. Run manually when ready.")
    
    return True


def main():
    """Main entry point for keybird-deploy"""
    
    if len(sys.argv) != 2:
        print("Usage: keybird-deploy <pi-name>")
        print()
        print("Deploy Keybird to a Raspberry Pi remotely using pi-shell.")
        print()
        print("Examples:")
        print("  keybird-deploy mypi")
        print("  keybird-deploy pi1")
        print()
        print("Prerequisites:")
        print("  1. Install pi-shell: pip install pi-shell")
        print("  2. Add your Pi: pi-shell add mypi --host 192.168.1.50 --user pi --password raspberry --push-key")
        print()
        sys.exit(1)
    
    pi_name = sys.argv[1]
    
    # Check prerequisites
    if not check_pi_shell():
        print("❌ pi-shell not found!")
        print()
        print("Install it first:")
        print("  pip install pi-shell")
        print()
        print("Then add your Pi:")
        print(f"  pi-shell add {pi_name} --host <IP> --user pi --password raspberry --push-key")
        sys.exit(1)
    
    if not check_pi_exists(pi_name):
        print(f"❌ Pi '{pi_name}' not found in pi-shell config!")
        print()
        print("Add it first:")
        print(f"  pi-shell add {pi_name} --host <IP> --user pi --password raspberry --push-key")
        print()
        print("Available Pis:")
        subprocess.run(['pi-shell', 'list'])
        sys.exit(1)
    
    # Deploy
    success = deploy_to_pi(pi_name)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()


