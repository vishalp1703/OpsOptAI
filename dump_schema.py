from sqlalchemy import create_engine, inspect

engine = create_engine("sqlite:///data/opspilot.db", future=True)
inspector = inspect(engine)

for table_name in sorted(inspector.get_table_names()):
    cols = inspector.get_columns(table_name)
    col_str = ", ".join(f"{c['name']}({c['type']})" for c in cols)
    print(f"\n{table_name}:")
    print(f"  {col_str}")