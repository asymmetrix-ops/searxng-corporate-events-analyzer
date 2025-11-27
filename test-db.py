from searxng_db import supabase

# simple test to see if table is accessible
data = supabase.table("valuation_reports").select("*").execute()
print(data.data)
