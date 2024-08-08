from barricade.db import engine

def compile_query(stmt, literal_params: bool = False):
    return str(stmt.compile(engine, compile_kwargs={"literal_binds": literal_params}))
