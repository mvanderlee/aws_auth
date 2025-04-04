import configparser
import logging
import os
import sys
from typing import Dict

import boto3
import botocore
import click
import coloredlogs
from environs import Env

coloredlogs.install(
    level=logging.INFO,
    fmt='%(message)s',
    level_styles=dict(coloredlogs.DEFAULT_LEVEL_STYLES, **{
        'info': {'color': 'green'},
        'debug': {'color': 'blue'},
    }),
)
logger = logging.getLogger(__name__)
Env().read_env(verbose=True)  # Load .env file


@click.group()
def cli():
    pass


@cli.command('mfa')
@click.option('-a', '--mfa-arn', envvar='MFA_ARN', default=None, help='The identification number of the MFA device that is associated with the IAM user. i.e.: "arn:aws:iam::123456789012:mfa/tony.stark". You can find this on the IAM page.')
@click.option('-c', '--code', envvar='CODE', prompt='Code', help='The code generated by your MFA device.')
@click.option('-d', '--duration', default=43_200, help='The duration, in seconds, of the session.')
@click.option('-sp', '--source-profile', default='mfa-source', envvar='SOURCE_PROFILE', help='What AWS profile to get the session token with.')
@click.option('-tp', '--target-profile', default='default', envvar='TARGET_PROFILE', help='What AWS profile to store the credentials under.')
@click.option('-v', '--verbose', default=False)
def mfa_cli(
    mfa_arn,
    code,
    duration,
    source_profile,
    target_profile,
    verbose,
    **kwargs,
):
    if not verbose:
        # Prevents "Found credentials in shared credentials file: ~/.aws/credentials" from showing every time
        logging.getLogger("botocore").setLevel(logging.WARN)

    '''Get MFA authenticated session credentials and save them to the aws credentials file'''

    session = get_session(source_profile)
    if mfa_arn is None:
        mfa_arn = auto_detect_mfa_device(session)

    sts = session.client('sts')
    session_token = sts.get_session_token(
        DurationSeconds=duration,
        SerialNumber=mfa_arn,
        TokenCode=code,
    )

    save_session_credentials(session_token['Credentials'], target_profile)


@cli.command('assume')
@click.option('-r', '--role-arn', prompt='Role Arn', help='The Arn of the Role to assume.')
@click.option('-n', '--session-name', prompt='Role Session Name', help='The identifier for the assumed role session.')
@click.option('-a', '--mfa-arn', envvar='MFA_ARN', default=None, help='The identification number of the MFA device that is associated with the IAM user. i.e.: "arn:aws:iam::123456789012:mfa/tony.stark". You can find this on the IAM page.')
@click.option('-c', '--code', envvar='CODE', prompt='Code', help='The code generated by your MFA device.')
@click.option('-d', '--duration', default=14_400, help='The duration, in seconds, of the session. (defaults to 4 hours)')
@click.option('-sp', '--source-profile', default='mfa-source', envvar='SOURCE_PROFILE', help='What AWS profile to get the session token with.')
@click.option('-tp', '--target-profile', default='default', envvar='TARGET_PROFILE', help='What AWS profile to store the credentials under.')
@click.option('-v', '--verbose', default=False)
def assume_cli(
    role_arn,
    session_name,
    mfa_arn,
    code,
    duration,
    source_profile,
    target_profile,
    verbose,
    **kwargs,
):
    '''
        Get MFA authenticated and assumed role session credentials and save them to the aws credentials file

        If you have multiple accounts you'd like to switch between, I recommend setting up
        aliases that call this script with predefined arguments.
    '''
    if not verbose:
        # Prevents "Found credentials in shared credentials file: ~/.aws/credentials" from showing every time
        logging.getLogger("botocore").setLevel(logging.WARN)

    session = get_session(source_profile)
    if mfa_arn is None:
        mfa_arn = auto_detect_mfa_device(session)

    sts = session.client('sts')
    session_token = sts.assume_role(
        DurationSeconds=duration,
        RoleArn=role_arn,
        RoleSessionName=session_name,
        SerialNumber=mfa_arn,
        TokenCode=code,
    )

    save_session_credentials(session_token['Credentials'], target_profile)


#########################################
# Support functions
#########################################
def auto_detect_mfa_device(session: boto3.Session) -> str:
    logger.debug('No MFA device specified, auto-detecting')
    iam = session.client('iam')
    mfa_devices = iam.list_mfa_devices().get('MFADevices')
    if len(mfa_devices) == 0:
        logger.error('No MFA device was specified and no MFA device was detected in IAM.')
        return sys.exit(1)
    elif len(mfa_devices) > 1:
        mfa_arn = mfa_devices[0]['SerialNumber']
        logger.warning(f'No MFA device was specified and multiple MFA device were detected in IAM. Will use the first one: {mfa_arn}')
    else:
        mfa_arn = mfa_devices[0]['SerialNumber']

    return mfa_arn


def get_session(source_profile: str) -> boto3.Session:
    try:
        session = boto3.Session(profile_name=source_profile)
    except botocore.exceptions.ProfileNotFound:
        logger.warning(f"AWS Profile '{source_profile}' could not be found, copying default profile")
        copy_profile(source_profile='default', target_profile=source_profile)
        session = boto3.Session(profile_name=source_profile)

    return session


def read_ini_file_to_dict(file_path):
    '''
        Read ini file and return it's content as a dict
    '''
    # Read ini file
    parser = configparser.ConfigParser()
    parser.read(file_path)
    # Convert to dictionary
    config_dict = {section: dict(parser.items(section)) for section in parser.sections()}

    return config_dict


def write_dict_to_ini_file(dict_, file_path):
    '''
        Write dict to ini file
    '''
    parser = configparser.ConfigParser()
    parser.read_dict(dict_)
    with open(file_path, 'w') as fout:
        parser.write(fout)


def save_session_credentials(
    session_credentials: Dict[str, str],
    target_profile: str,
):
    # Get credentials from user's home
    creds_path = os.path.join(os.path.expanduser('~'), '.aws', 'credentials')
    aws_creds = read_ini_file_to_dict(creds_path)

    # Update credentials
    aws_creds.update({
        target_profile: {
            'aws_access_key_id': session_credentials['AccessKeyId'],
            'aws_secret_access_key': session_credentials['SecretAccessKey'],
            'aws_session_token': session_credentials['SessionToken'],
        },
    })

    # Overwrite file with new credentials
    write_dict_to_ini_file(aws_creds, creds_path)
    logger.info(f"Updated {creds_path}, use profile '{target_profile}' for your AWS requests.")


def copy_profile(
    source_profile: str,
    target_profile: str,
):
    # Get credentials from user's home
    creds_path = os.path.join(os.path.expanduser('~'), '.aws', 'credentials')
    aws_creds = read_ini_file_to_dict(creds_path)

    aws_creds[target_profile] = aws_creds[source_profile]
    # Overwrite file with new credentials
    write_dict_to_ini_file(aws_creds, creds_path)


if __name__ == "__main__":
    cli()
