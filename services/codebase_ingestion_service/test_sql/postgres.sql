CREATE TABLE employees
(
    id SERIAL PRIMARY KEY,
    name TEXT,
    salary NUMERIC
);

CREATE OR REPLACE FUNCTION get_employees()
RETURNS TABLE(id INT, name TEXT)
LANGUAGE plpgsql
AS
$$
BEGIN
    RETURN QUERY
    SELECT id, name
    FROM employees;
END;
$$;