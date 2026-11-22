"""Language-specific structural extraction regression tests for Pass 2."""

from coding_agent.parsers import (
    CParser,
    CppParser,
    CSharpParser,
    GoParser,
    JavaParser,
    RustParser,
)


def find_symbol(parsed, name: str, kind: str):
    return next(
        symbol for symbol in parsed.symbols
        if symbol.name == name and symbol.kind == kind
    )


def test_go_extracts_package_types_fields_receiver_methods_and_exports():
    source = '''\
package demo

type Counter struct {
    Value int
}

func (c *Counter) Add(delta int) int {
    return c.Value + delta
}

func NewCounter() *Counter {
    return &Counter{}
}
'''
    parsed = GoParser().parse("counter.go", source)

    package = find_symbol(parsed, "demo", "package")
    counter = find_symbol(parsed, "Counter", "struct")
    field = find_symbol(parsed, "Value", "field")
    method = find_symbol(parsed, "Add", "method")
    constructor = find_symbol(parsed, "NewCounter", "function")

    assert package.line == 1
    assert counter.qualified_name == "demo.Counter"
    assert field.qualified_name == "demo.Counter.Value"
    assert field.declared_type == "int"
    assert method.qualified_name == "demo.Counter.Add"
    assert dict(method.metadata)["receiver"] == "(c *Counter)"
    assert counter.is_exported is True
    assert constructor.is_exported is True


def test_rust_extracts_traits_impls_fields_variants_and_visibility():
    source = '''\
#[deprecated]
pub struct User {
    pub id: u64,
}

pub enum Status {
    Ready,
    Failed(String),
}

pub trait Repository {
    fn find(&self, id: u64) -> Option<User>;
}

impl Repository for User {
    pub fn find(&self, id: u64) -> Option<User> {
        None
    }
}
'''
    parsed = RustParser().parse("lib.rs", source)

    user = find_symbol(parsed, "User", "struct")
    field = find_symbol(parsed, "id", "field")
    status = find_symbol(parsed, "Status", "enum")
    ready = find_symbol(parsed, "Ready", "variant")
    trait = find_symbol(parsed, "Repository", "trait")
    implementation = find_symbol(parsed, "User", "implementation")

    assert user.is_exported is True
    assert "#[deprecated]" in user.decorators
    assert dict(user.metadata)["visibility"] == "pub"
    assert field.qualified_name == "User.id"
    assert field.declared_type == "u64"
    assert status.qualified_name == "Status"
    assert ready.qualified_name == "Status.Ready"
    assert trait.qualified_name == "Repository"
    assert dict(implementation.metadata)["implements"] == "Repository"


def test_c_extracts_typedef_struct_members_enum_macros_and_function_prototypes():
    source = '''\
#define LIMIT 100

typedef struct Point {
    int x;
    int y;
} Point;

enum Color { RED, GREEN, BLUE };

int add(int left, int right);
'''
    parsed = CParser().parse("geometry.h", source)

    macro = find_symbol(parsed, "LIMIT", "macro")
    point = find_symbol(parsed, "Point", "struct")
    x = find_symbol(parsed, "x", "field")
    color = find_symbol(parsed, "Color", "enum")
    red = find_symbol(parsed, "RED", "enum_value")
    add = find_symbol(parsed, "add", "function_declaration")

    assert macro.line == 1
    assert point.qualified_name == "Point"
    assert x.qualified_name == "Point.x"
    assert x.declared_type == "int"
    assert color.qualified_name == "Color"
    assert red.qualified_name == "Color.RED"
    assert add.signature == "(int left, int right)"


def test_cpp_extracts_namespaces_templates_members_and_access_levels():
    source = '''\
namespace demo {
template <typename T>
class Box : public Base {
public:
    Box(T value);
    T get() const { return value; }
private:
    T value;
};
}
'''
    parsed = CppParser().parse("box.hpp", source)

    namespace = find_symbol(parsed, "demo", "namespace")
    box = find_symbol(parsed, "Box", "class")
    constructor = find_symbol(parsed, "Box", "constructor")
    getter = find_symbol(parsed, "get", "method")
    value = find_symbol(parsed, "value", "field")

    assert namespace.qualified_name == "demo"
    assert box.qualified_name == "demo.Box"
    assert dict(box.metadata)["inherits"] == "public Base"
    assert dict(constructor.metadata)["access"] == "public"
    assert dict(getter.metadata)["access"] == "public"
    assert dict(value.metadata)["access"] == "private"
    assert value.declared_type == "T"
    assert dict(box.metadata)["template"] == "true"


def test_csharp_extracts_file_scoped_namespace_records_fields_properties_and_bases():
    source = '''\
namespace Demo.Services;

[Obsolete]
public record User(int Id)
{
    public string Name { get; init; } = "";
}

public class UserService : BaseService, IUserService
{
    private readonly User _user;
    public User GetUser() => _user;
}
'''
    parsed = CSharpParser().parse("UserService.cs", source)

    namespace = find_symbol(parsed, "Demo.Services", "namespace")
    user = find_symbol(parsed, "User", "record")
    name = find_symbol(parsed, "Name", "property")
    service = find_symbol(parsed, "UserService", "class")
    field = find_symbol(parsed, "_user", "field")
    method = find_symbol(parsed, "GetUser", "method")

    assert namespace.qualified_name == "Demo.Services"
    assert user.is_exported is True
    assert "[Obsolete]" in user.decorators
    assert name.qualified_name == "Demo.Services.User.Name"
    assert dict(service.metadata)["base_types"] == "BaseService, IUserService"
    assert "private" in field.modifiers
    assert "readonly" in field.modifiers
    assert field.declared_type == "User"
    assert "public" in method.modifiers


def test_java_extracts_fields_nested_types_inheritance_and_enum_constants():
    source = '''\
package demo.services;

public class UserService extends BaseService implements UserRepository {
    private final String name;

    public UserService(String name) {
        this.name = name;
    }

    @Deprecated
    public String getName() { return name; }

    public static class Nested { }
}

enum Status { READY, FAILED }
'''
    parsed = JavaParser().parse("UserService.java", source)

    service = find_symbol(parsed, "UserService", "class")
    field = find_symbol(parsed, "name", "field")
    method = find_symbol(parsed, "getName", "method")
    nested = find_symbol(parsed, "Nested", "class")
    ready = find_symbol(parsed, "READY", "enum_value")

    assert service.qualified_name == "demo.services.UserService"
    assert service.is_exported is True
    assert dict(service.metadata)["extends"] == "BaseService"
    assert dict(service.metadata)["implements"] == "UserRepository"
    assert field.qualified_name == "demo.services.UserService.name"
    assert "private" in field.modifiers
    assert "final" in field.modifiers
    assert field.declared_type == "String"
    assert "public" in method.modifiers
    assert "@Deprecated" in method.decorators
    assert nested.qualified_name == "demo.services.UserService.Nested"
    assert ready.qualified_name == "demo.services.Status.READY"
