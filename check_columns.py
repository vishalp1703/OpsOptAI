import sqlalchemy as sa
from sqlalchemy import inspect

engine = sa.create_engine('sqlite:///data/opspilot.db')
insp = inspect(engine)

for t in ['root_causes', 'recommendations', 'jira_stories', 'executive_summaries']:
    print(t, '->', [c['name'] for c in insp.get_columns(t)])