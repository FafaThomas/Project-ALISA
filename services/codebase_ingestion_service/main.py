from service import CodebaseIngestionService


PROJECT = r"C:\Users\tdecl\Downloads\ICTI_Website-main\ICTI_Website-main"

ingestion = CodebaseIngestionService()

ingestion.create_project(PROJECT)