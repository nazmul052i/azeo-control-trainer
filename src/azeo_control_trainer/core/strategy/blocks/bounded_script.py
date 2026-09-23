"""Bounded interpreter for authored controller calculations, never Python exec."""
from __future__ import annotations

import ast
from copy import deepcopy
from functools import lru_cache
import operator

MAX_ITEMS = 512
MAX_VALUES = 4096
MAX_STRING = 8192
MAX_BITS = 256
MAX_OPERATIONS = 4096
MAX_SOURCE = 16384


def checked(value):
    # Count expanded contents as well as depth: small aliased lists can have
    # enormous traversal cost without occupying much heap themselves.
    pending = [(value, 0)]
    count = 0
    characters = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > MAX_VALUES or depth > 16:
            raise ValueError("Script value exceeds the nesting/item budget")
        kind = type(item)
        if kind is int and item.bit_length() > MAX_BITS:
            raise ValueError("Script integer exceeds 256 bits")
        if kind is str:
            characters += len(item)
            if len(item) > MAX_STRING or characters > 65536:
                raise ValueError("Script string exceeds the character budget")
        if kind in (list, tuple, dict):
            if len(item) > MAX_ITEMS:
                raise ValueError("Script container exceeds 512 items")
            values = (*item.keys(), *item.values()) if kind is dict else item
            pending.extend((child, depth + 1) for child in values)
        elif kind not in (type(None), bool, int, float, str):
            raise ValueError(f"Script value type {kind.__name__} is not supported")
    return value


_BINOPS = {"Add": operator.add, "Sub": operator.sub, "Mult": operator.mul,
           "Div": operator.truediv, "FloorDiv": operator.floordiv, "Mod": operator.mod,
           "Pow": operator.pow, "BitAnd": operator.and_, "BitOr": operator.or_, "BitXor": operator.xor}
_COMPARE = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
            ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}


def binary(op, left, right):
    checked(left)
    checked(right)
    if op == "Pow" and type(left) is int and type(right) is int and right >= 0:
        if abs(left) > 1 and left.bit_length() * right > MAX_BITS:
            raise ValueError("Script power exceeds the integer budget")
    if op == "Mult":
        sequence, count = (left, right) if type(left) in (str, list, tuple) else (right, left)
        if type(sequence) in (str, list, tuple) and isinstance(count, int):
            limit = MAX_STRING if type(sequence) is str else MAX_ITEMS
            if len(sequence) * max(0, count) > limit:
                raise ValueError("Script multiplication exceeds the allocation budget")
    if op == "Mod" and type(left) is str:
        raise ValueError("String formatting is not part of the script language")
    return checked(_BINOPS[op](left, right))


_EXPRESSIONS = (ast.Expression, ast.Constant, ast.Name, ast.Load, ast.BinOp,
               ast.UnaryOp, ast.Compare, ast.BoolOp, ast.IfExp, ast.Call, ast.keyword,
               ast.Subscript, ast.List, ast.Tuple, ast.Dict,
               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
               ast.USub, ast.UAdd, ast.Not, ast.Eq, ast.NotEq, ast.Lt, ast.LtE,
               ast.Gt, ast.GtE, ast.And, ast.Or, ast.BitAnd, ast.BitOr, ast.BitXor)
_STATEMENTS = (ast.Module, ast.Assign, ast.AugAssign, ast.Store, ast.Expr,
               ast.If, ast.For, ast.While, ast.Break, ast.Continue, ast.Pass, ast.Attribute)
_METHODS = {dict: {"get", "setdefault", "pop", "clear", "copy"},
            list: {"append", "extend", "pop", "clear", "copy"}}


@lru_cache(maxsize=256)
def parse(source, mode="eval", filename="<controller_script>"):
    if not isinstance(source, str) or len(source) > MAX_SOURCE:
        raise ValueError("Script source exceeds 16384 characters")
    tree = ast.parse(source, filename, mode)
    pending = [(tree, 0)]
    count = 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if count > 1024 or depth > 48:
            raise ValueError("Script syntax exceeds the node/depth budget")
        allowed = _EXPRESSIONS + _STATEMENTS if mode == "exec" else _EXPRESSIONS
        if not isinstance(node, allowed):
            raise ValueError(f"{type(node).__name__} is not part of the script language")
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            raise ValueError("Private names are not part of the script language")
        if isinstance(node, ast.Constant):
            checked(node.value)
        if isinstance(node, ast.Attribute) and node.attr not in set.union(*_METHODS.values()):
            raise ValueError("Only bounded list/dictionary methods are allowed")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, (ast.Name, ast.Attribute)):
                raise ValueError("Only named functions and bounded methods are allowed")
            if any(keyword.arg is None or keyword.arg.startswith("_") for keyword in node.keywords):
                raise ValueError("Keyword unpacking and private keywords are not allowed")
        pending.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    return tree


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


class Evaluation:
    def __init__(self, namespace):
        self.namespace = namespace
        self.functions = {name: value for name, value in namespace.items() if callable(value)}
        self.remaining = MAX_OPERATIONS

    def tick(self):
        self.remaining -= 1
        if self.remaining < 0:
            raise ValueError("Script execution budget exceeded")

    def expression(self, node):
        self.tick()
        if isinstance(node, ast.Constant):
            return checked(node.value)
        if isinstance(node, ast.Name):
            if node.id not in self.namespace:
                raise ValueError(f"Unknown variable: {node.id}")
            return checked(self.namespace[node.id])
        if isinstance(node, (ast.List, ast.Tuple)):
            values = [self.expression(value) for value in node.elts]
            return checked(tuple(values) if isinstance(node, ast.Tuple) else values)
        if isinstance(node, ast.Dict):
            return checked({self.expression(key): self.expression(value)
                            for key, value in zip(node.keys, node.values)})
        if isinstance(node, ast.BinOp):
            return binary(type(node.op).__name__, self.expression(node.left), self.expression(node.right))
        if isinstance(node, ast.UnaryOp):
            op = {ast.Not: operator.not_, ast.USub: operator.neg, ast.UAdd: operator.pos}[type(node.op)]
            return checked(op(self.expression(node.operand)))
        if isinstance(node, ast.BoolOp):
            result = self.expression(node.values[0])
            for value in node.values[1:]:
                if isinstance(node.op, ast.And) and not result or isinstance(node.op, ast.Or) and result:
                    break
                result = self.expression(value)
            return result
        if isinstance(node, ast.IfExp):
            return self.expression(node.body if self.expression(node.test) else node.orelse)
        if isinstance(node, ast.Compare):
            left = self.expression(node.left)
            for op, comparator in zip(node.ops, node.comparators):
                right = self.expression(comparator)
                if not _COMPARE[type(op)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.Subscript):
            return checked(self.expression(node.value)[self.expression(node.slice)])
        if isinstance(node, ast.Call):
            arguments = [self.expression(argument) for argument in node.args]
            keywords = {keyword.arg: self.expression(keyword.value) for keyword in node.keywords}
            if isinstance(node.func, ast.Attribute):
                owner = self.expression(node.func.value)
                method = node.func.attr
                if method not in _METHODS.get(type(owner), ()):
                    raise ValueError("Method is not allowed on this value")
                if method == "extend" and (not arguments or type(arguments[0]) not in (list, tuple)):
                    raise ValueError("extend requires a bounded list or tuple")
                result = getattr(owner, method)(*arguments, **keywords)
                checked(owner)
            else:
                name = node.func.id
                if name == "pow":
                    if len(arguments) != 2 or keywords:
                        raise ValueError("pow requires two arguments")
                    result = binary("Pow", *arguments)
                elif name in self.functions:
                    # Only host-installed helpers are callable; authored code
                    # cannot introduce functions, imports or object access.
                    result = self.functions[name](*arguments, **keywords)
                else:
                    raise ValueError(f"Unknown function: {name}")
            return checked(result)
        raise ValueError(f"Unsupported expression: {type(node).__name__}")

    def assign(self, target, value):
        checked(value)
        if isinstance(target, ast.Name):
            if target.id in self.functions or target.id == "state":
                raise ValueError(f"Cannot replace reserved name: {target.id}")
            self.namespace[target.id] = value
        elif isinstance(target, ast.Subscript):
            owner = self.expression(target.value)
            owner[self.expression(target.slice)] = value
            checked(owner)
        elif isinstance(target, (ast.List, ast.Tuple)):
            if type(value) not in (list, tuple) or len(value) != len(target.elts):
                raise ValueError("Assignment unpacking requires matching bounded values")
            for child, item in zip(target.elts, value):
                self.assign(child, item)
        else:
            raise ValueError("Unsupported assignment target")

    def statements(self, statements):
        for node in statements:
            self.tick()
            if isinstance(node, ast.Assign):
                value = self.expression(node.value)
                for target in node.targets:
                    self.assign(target, value)
            elif isinstance(node, ast.AugAssign):
                self.assign(node.target, binary(type(node.op).__name__, self.expression(node.target),
                                                self.expression(node.value)))
            elif isinstance(node, ast.Expr):
                self.expression(node.value)
            elif isinstance(node, ast.If):
                self.statements(node.body if self.expression(node.test) else node.orelse)
            elif isinstance(node, (ast.For, ast.While)):
                iterator = iter(self.expression(node.iter)) if isinstance(node, ast.For) else None
                while True:
                    self.tick()
                    if iterator is None:
                        if not self.expression(node.test):
                            self.statements(node.orelse)
                            break
                    else:
                        try:
                            value = next(iterator)
                        except StopIteration:
                            self.statements(node.orelse)
                            break
                        self.assign(node.target, value)
                    try:
                        self.statements(node.body)
                    except _Break:
                        break
                    except _Continue:
                        continue
            elif isinstance(node, ast.Break):
                raise _Break()
            elif isinstance(node, ast.Continue):
                raise _Continue()
            elif not isinstance(node, ast.Pass):
                raise ValueError(f"Unsupported statement: {type(node).__name__}")


def evaluate(tree, namespace, validate=None):
    original = namespace.get("state", {})
    if validate:
        namespace["state"] = deepcopy(checked(original))
    result = checked(Evaluation(namespace).expression(tree.body))
    if validate:
        result = validate(result)
        checked(namespace["state"])
        original.clear()
        original.update(namespace["state"])
    return result


def execute(tree, namespace, validate=None):
    original = namespace.get("state", {})
    namespace["state"] = deepcopy(checked(original))
    Evaluation(namespace).statements(tree.body)
    checked(namespace["state"])
    result = validate(namespace) if validate else None
    original.clear()
    original.update(namespace["state"])
    return result
