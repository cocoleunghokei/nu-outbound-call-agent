-- Fails if any table in the exposed `public` schema has row-level security off.
--
-- Why: Supabase serves the `public` schema over its REST API. A table without
-- RLS can be read and written by anyone holding the public (anon) key, which
-- ships to every browser. CONTRIBUTING.md requires RLS in the same migration
-- that creates the table; this query is the automated check for that rule.
do $$
declare
  missing text;
begin
  select string_agg(format('%I.%I', n.nspname, c.relname), ', ' order by c.relname)
    into missing
  from pg_class c
  join pg_namespace n on n.oid = c.relnamespace
  where n.nspname = 'public'
    and c.relkind in ('r', 'p')  -- ordinary and partitioned tables
    and not c.relrowsecurity;

  if missing is not null then
    raise exception 'Row-level security is disabled on: %. Add "alter table ... enable row level security;" to the migration that creates each table.', missing;
  end if;
end
$$;
