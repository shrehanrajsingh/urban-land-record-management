-- Mirror of Alembic trigger for documentation / initdb mounts.
-- Applied primarily via alembic migration 001.

CREATE OR REPLACE FUNCTION enforce_parcel_status_transition()
RETURNS trigger AS $$
DECLARE
  role text := COALESCE(current_setting('app.current_role', true), NEW.created_by_role, 'SYSTEM');
BEGIN
  IF role = 'AI_SERVICE' AND NEW.status IN ('APPROVED', 'PUBLISHED') THEN
    RAISE EXCEPTION 'AI_SERVICE cannot set status %', NEW.status
      USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
