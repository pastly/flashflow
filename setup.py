#!/usr/bin/env python3
# Always prefer setuptools over distutils
from setuptools import setup, find_packages
# To use a consistent encoding
from codecs import open
import os
from flashflow import __version__


here = os.path.abspath(os.path.dirname(__file__))


def long_description():
    with open(os.path.join(here, 'README.md'), encoding='utf-8') as f:
        return f.read()


def get_package_data():
    return [
        'config.default.ini',
        'config.log.default.ini',
    ]


def get_data_files():
    pass


setup(
    name='flashflow',
    version=__version__,
    description='FlashFlow: A Secure Speed Test for Tor',
    long_description=long_description(),
    long_description_content_type="text/markdown",
    author='Matt Traudt',
    author_email='pastly@torproject.org',
    license='CC0',
    # url="https://github.com/pastly/flashflow",
    classifiers=[
        'Development Status :: 4 - Alpha',
        "Environment :: Console",
        'Intended Audience :: Developers',
        'Intended Audience :: System Administrators',
        'Operating System :: OS Independent',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Topic :: System :: Networking',
    ],
    packages=find_packages(),
    include_package_data=True,
    package_data={
        'flashflow': get_package_data(),
    },
    data_files=get_data_files(),
    keywords='tor onion bandwidth measurements scanner relay',
    python_requires='>=3.6',
    entry_points={
        'console_scripts': [
            'flashflow = flashflow.flashflow:main',
        ]
    },
    install_requires=[
        'stem==1.8.0',
        'transitions==0.8.1',
        # 'requests[socks]',
    ],
    extras_require={
        # vulture: find unused code
        'dev': ['flake8', 'vulture', 'mypy'],
        'test': ['tox', 'pytest', 'coverage'],
    },
)
