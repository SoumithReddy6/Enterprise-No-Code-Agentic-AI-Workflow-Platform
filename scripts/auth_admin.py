"""Local operator recovery and invitation issuance. Requires database filesystem/DB access."""
import argparse
import os
from pathlib import Path
from sqlalchemy import create_engine
from dotenv import load_dotenv
from backend.app.auth import issue_invite
from backend.app.auth_security import unlock_login
from backend.app.storage import Base


def main():
    load_dotenv()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url',default=os.getenv('DATABASE_URL','sqlite:///'+str(Path(os.getenv('DATA_DIR','.data'))/'workflows.db')))
    commands=parser.add_subparsers(dest='command',required=True)
    invite=commands.add_parser('invite',help='Print a one-use token; requires AUTH_REGISTRATION_MODE=invite to redeem')
    invite.add_argument('--email',default='',help='Optionally bind token to this email')
    invite.add_argument('--hours',type=int,default=24)
    unlock=commands.add_parser('unlock',help='Clear an account and/or IP login cooldown; does not change passwords')
    unlock.add_argument('--email')
    unlock.add_argument('--ip')
    args=parser.parse_args()
    engine=create_engine(args.database_url,connect_args={'timeout':30} if args.database_url.startswith('sqlite') else {})
    try:
        Base.metadata.create_all(engine)
        if args.command=='invite':print(issue_invite(engine,email=args.email,hours=args.hours))
        else:print(f'Cleared {unlock_login(engine,email=args.email,address=args.ip)} login budgets.')
    except ValueError as exc:parser.error(str(exc))
    finally:engine.dispose()

if __name__=='__main__':main()
