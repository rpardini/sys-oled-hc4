#!/bin/bash
# Install script for sys-oled

INSTALL_PATH="/usr/local/"
SYSTEMD_PATH="/etc/systemd/"
BUILD_DEPS="python3-dev python3-pip python3-setuptools python3-wheel python3-psutil libfreetype6-dev libjpeg-dev build-essential"
RUNTIME_DEPS="python3 python3-psutil python3-luma.oled python3-rpi.gpio"

if [ "$(id -u)" != "0" ]; then
	echo "This script must be run as root!"
	exit 1
fi

echo "Installing Dependencies"
apt-get update

if [[  "$(lsb_release -cs)" == "noble" || "$(lsb_release -cs)" == "jammy" || "$(lsb_release -cs)" == "kinetic"  || "$(lsb_release -cs)" == "lunar"  || "$(lsb_release -cs)" == "bookworm"  || "$(lsb_release -cs)" == "trixie" || "$(lsb_release -cs)" == "sid" ]]; then
	echo "$(lsb_release -cs); use Ubuntu-packaged Python deps."
	apt-get install -y ${RUNTIME_DEPS}
else
	echo "Not jammy, installing build deps, then using pip for python stuff. This is gonna take a while."
	apt-get install -y $BUILD_DEPS
	# luma.oled depends on this thing, but released non-alpha version does not build under gcc10
	echo "Installing GPIO library dependency in pip..."
	pip3 install RPi.GPIO==0.7.1a4

	echo "Installing luma.oled library"
	pip3 install --upgrade luma.oled
fi

echo "Installing sys-oled files"
cp -fv etc/sys-oled.conf /etc
cp -frv bin "$INSTALL_PATH"
cp -frv share "$INSTALL_PATH"
cp -frv system "$SYSTEMD_PATH"

echo "Enabling sys-oled at startup"
systemctl daemon-reload
systemctl enable sys-oled.service

echo "Starting service..."
systemctl start sys-oled.service
