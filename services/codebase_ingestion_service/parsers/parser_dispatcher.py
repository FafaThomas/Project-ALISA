from parsers.python_parser import PythonParser
from parsers.csharp_parser import CSharpParser
from parsers.generic_parser import GenericParser
from parsers.javascript_parser import JavaScriptParser
from parsers.html_parser import HTMLParser
from parsers.css_parser import CSSParser



class ParserDispatcher:

    def __init__(self):

        self.parsers = {

            "tree_sitter_python":
                PythonParser(),

            "tree_sitter_c_sharp":
                CSharpParser(),

            "tree_sitter_javascript":
                JavaScriptParser(),

            "tree_sitter_html":
                HTMLParser(),

            "tree_sitter_css":
                CSSParser(),

            "generic":
                GenericParser(),

        }

    def get_parser(self, parser):

        return self.parsers.get(
            parser,
            self.parsers["generic"]
        )