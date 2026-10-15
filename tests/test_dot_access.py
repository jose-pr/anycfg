"""DotAccessibleDict: conversion happens once, and reads never write."""

import collections

import pytest

import yaconfiglib
from yaconfiglib import DotAccessibleDict, Loader
from yaconfiglib._dotaccess import to_dot_access


def _load(data, **settings):
    return Loader(wrap=True, **settings).load(data)


class TestWrapSetting:
    def test_wrap_true_returns_dot_accessible_mappings(self):
        config = _load({"db": {"host": "localhost", "port": 3306}})
        assert type(config) is DotAccessibleDict
        assert config.db.host == "localhost"
        assert config.db.port == 3306
        assert config.get("db.host") == "localhost"
        assert config.get("db.missing", "default") == "default"

    def test_wrap_with_the_class_is_the_same_as_true(self):
        config = Loader(wrap=DotAccessibleDict).load({"db": {"host": "h"}})
        assert type(config) is DotAccessibleDict and config.db.host == "h"

    def test_no_wrap_returns_plain_data(self):
        config = Loader().load({"db": {"host": "h"}})
        assert type(config) is dict and type(config["db"]) is dict
        assert type(Loader(wrap=False).load({"a": 1})) is dict

    def test_wrap_callable_is_applied_to_the_result(self):
        assert Loader(wrap=sorted).load({"b": 1, "a": 2}) == ["a", "b"]

    def test_wrap_as_a_per_call_override(self):
        loader = Loader()
        assert type(loader.load({"a": 1}, wrap=True)) is DotAccessibleDict
        assert type(loader.load({"a": 1})) is dict

    def test_non_mapping_result_keeps_its_shape(self):
        config = Loader(wrap=True, collect="list").load({"a": {"b": 1}}, {"c": 2})
        assert type(config) is list
        assert config[0].a.b == 1 and config[1].c == 2

    def test_hash_collection_is_dot_accessible(self):
        hashed = Loader(wrap=True, collect="hash", key=lambda name, doc: "doc").load(
            {"a": {"b": 1}}
        )
        assert hashed.doc.a.b == 1

    def test_load_all_documents_are_dot_accessible(self):
        documents = list(Loader(wrap=True).load_all({"a": [{"b": 1}]}, {"c": 2}))
        assert documents[0].a[0].b == 1
        assert documents[1].c == 2


class TestConvertOnce:
    def test_get_missing_key_returns_default_without_inserting(self):
        default = {}
        config = _load({"db": {"host": "h"}})
        assert config.get("logging", default) is default
        other = ["fallback"]
        assert config.get("logging", other) is other
        assert config.get("x.y", {}, dig=False) == {}
        assert list(config) == ["db"]

    def test_item_and_attribute_access_share_one_object(self):
        config = _load({"db": {"host": "h"}})
        assert config["db"].host == "h"
        assert config["db"] is config.db
        assert isinstance(config["db"], DotAccessibleDict)

    def test_nested_values_converted_before_any_read(self):
        config = _load({"db": {"credentials": {"user": "postgres"}}})
        assert isinstance(config["db"]["credentials"], DotAccessibleDict)
        assert config.get("db.credentials.user") == "postgres"

    def test_reads_never_replace_stored_values(self):
        config = _load({"db": {"credentials": {"user": "u"}}})
        held = config["db"]
        held_nested = held["credentials"]

        assert config.db is held
        assert config.get("db.credentials.user") == "u"
        config.db.port = 5432

        assert config["db"] is held
        assert held["credentials"] is held_nested
        assert held["port"] == 5432

    def test_dicts_inside_lists_and_tuples_are_dot_accessible(self):
        config = DotAccessibleDict(
            {
                "services": [{"name": "api"}],
                "nested": [[{"name": "deep"}]],
                "pair": ({"name": "tup"},),
            }
        )
        assert config.services[0].name == "api"
        assert config.nested[0][0].name == "deep"
        assert config.pair[0].name == "tup"
        assert type(config.pair) is tuple

    @pytest.mark.usefixtures("needs_yaml")
    def test_yaml_anchor_aliases_stay_shared(self, tmp_path):
        (tmp_path / "main.yaml").write_text(
            "defaults: &defaults\n  pool: 5\nsvc_a: *defaults\nsvc_b: *defaults\n",
            encoding="utf-8",
        )
        config = Loader(base_dir=tmp_path, wrap=True).load("main.yaml")
        assert config["svc_a"] is config["svc_b"]
        assert config.svc_a is config.svc_b

    @pytest.mark.usefixtures("needs_yaml")
    def test_inline_and_included_list_entries_are_both_dot_accessible(self, tmp_path):
        (tmp_path / "srv.yaml").write_text("name: included\n", encoding="utf-8")
        (tmp_path / "main.yaml").write_text(
            "services:\n  - name: inline\n  - !include srv.yaml\n", encoding="utf-8"
        )
        config = Loader(base_dir=tmp_path, wrap=True).load("main.yaml")
        assert config.services[0].name == "inline"
        assert config.services[1].name == "included"

    def test_load_never_aliases_source_containers(self):
        source = {"db": {"host": "h"}, "l": [{"n": 1}]}
        config = _load(source)
        config["db"]["host"] = "changed"
        config.l[0].n = 2
        config.l.append({"n": 3})
        assert source == {"db": {"host": "h"}, "l": [{"n": 1}]}

    def test_to_dot_access_copies_instead_of_mutating(self):
        source = {"db": {"host": "h"}, "l": [{"n": 1}]}
        converted = to_dot_access(source)
        assert type(converted) is DotAccessibleDict
        assert type(source["db"]) is dict and type(source["l"][0]) is dict
        assert converted["db"] is not source["db"]
        assert to_dot_access([{"a": 1}])[0].a == 1
        assert to_dot_access(5) == 5

    def test_to_dot_access_keeps_aliasing(self):
        shared = {"pool": 5}
        converted = to_dot_access({"a": shared, "b": shared, "l": [shared]})
        assert converted.a is converted.b is converted.l[0]

    def test_dict_subclass_values_convert_the_same_way_for_every_access(self):
        counters = collections.defaultdict(int)
        config = DotAccessibleDict({"counters": counters})
        assert type(config["counters"]) is DotAccessibleDict
        assert config.counters is config["counters"]
        assert config.get("counters.misses", "D") == "D"
        with pytest.raises(KeyError):
            config["counters"]["misses"]
        assert dict(counters) == {}

    def test_non_dict_mappings_and_list_subclasses_are_left_alone(self):
        class Tagged(list):
            pass

        proxy = __import__("types").MappingProxyType({"a": {"b": 1}})
        tagged = Tagged([{"a": 1}])
        config = DotAccessibleDict({"proxy": proxy, "tagged": tagged})
        assert config["proxy"] is proxy
        assert config["tagged"] is tagged

    def test_self_referencing_mapping_converts(self):
        cyclic = {}
        cyclic["me"] = cyclic
        cyclic["l"] = [cyclic]
        converted = DotAccessibleDict(cyclic)
        assert converted.me is converted
        assert converted.l[0] is converted

    def test_direct_construction_converts_nested_values(self):
        positional = DotAccessibleDict({"a": {"b": 1}})
        assert type(positional["a"]) is DotAccessibleDict
        assert positional.a.b == 1
        keyword = DotAccessibleDict(a={"b": 1})
        assert type(keyword["a"]) is DotAccessibleDict

    def test_values_written_after_construction_are_stored_as_given(self):
        config = _load({"db": {"host": "h"}})
        plain = {"port": 5432}
        config["extra"] = plain
        assert config["extra"] is plain
        assert type(config["extra"]) is dict
        config.other = plain
        assert config["other"] is plain


class TestGetPaths:
    """What `get()` accepts as a path, and what it returns when one breaks."""

    def test_dig_false_disables_traversal(self):
        config = _load({"db": {"host": "localhost"}})
        assert config.get("db.host", "default", dig=False) == "default"

    def test_exact_key_outranks_traversal(self):
        config = _load({"db.host": "exact", "db": {"host": "traversed"}})
        assert config.get("db.host") == "exact"
        assert config.get("db.host", dig=False) == "exact"

    def test_missing_non_string_key_returns_default(self):
        config = DotAccessibleDict({404: "not_found"})
        assert config.get(404) == "not_found"
        assert config.get(500) is None
        assert config.get(None, "n") == "n"
        assert config.get(1.5, "f") == "f"

    def test_unhashable_key_raises(self):
        with pytest.raises(TypeError):
            DotAccessibleDict({}).get(["a"])

    def test_nested_null_leaf_is_returned(self):
        config = _load({"password": None, "db": {"password": None}})
        assert config.get("password", "x") is None
        assert config.get("db.password", "x") is None
        assert config.db.password is None

    def test_null_intermediate_returns_default(self):
        config = _load({"db": None})
        assert config.get("db.password", "x") == "x"
        assert config.get(("db", "password"), "x") == "x"

    def test_scalar_intermediate_returns_default(self):
        config = _load({"db": "text"})
        assert config.get("db.0", "x") == "x"
        assert config.get(("db", 0), "x") == "x"

    def test_dotted_path_indexes_lists(self):
        config = _load({"servers": [{"host": "a"}, [{"y": 0}, {"y": 1}]]})
        assert config.get("servers.0.host") == "a"
        assert config.get("servers.1.1.y") == 1

    def test_dotted_path_indexes_tuples(self):
        config = DotAccessibleDict({"pair": ({"host": "a"}, {"host": "b"})})
        assert config.get("pair.1.host") == "b"

    @pytest.mark.parametrize("segment", ["5", "-1", "x", "\u0661"])
    def test_bad_index_segment_returns_default(self, segment):
        # U+0661 is ARABIC-INDIC DIGIT ONE: a digit, but not an ASCII one.
        config = _load({"servers": [{"host": "a"}, {"host": "b"}]})
        assert config.get(f"servers.{segment}.host", "X") == "X"

    def test_digit_segment_on_a_mapping_is_a_string_key(self):
        config = DotAccessibleDict({"codes": {"0": "zero", 0: "int"}})
        assert config.get("codes.0") == "zero"
        assert DotAccessibleDict({"codes": {0: "int"}}).get("codes.0", "D") == "D"

    def test_tuple_path_reaches_keys_containing_dots(self):
        config = _load({"metadata": {"labels": {"app.kubernetes.io/name": "web"}}})
        assert config.get(("metadata", "labels", "app.kubernetes.io/name")) == "web"

    def test_tuple_path_uses_int_keys_and_indexes(self):
        config = DotAccessibleDict({"codes": {404: "nf"}, "servers": [{"host": "a"}]})
        assert config.get(("codes", 404)) == "nf"
        assert config.get(("servers", 0, "host")) == "a"
        # A digit string is not an index, and True is not 1.
        assert config.get(("servers", "0", "host"), "D") == "D"
        assert config.get(("servers", True, "host"), "D") == "D"
        assert config.get(("servers", -1, "host"), "D") == "D"

    def test_exact_tuple_key_outranks_traversal(self):
        config = DotAccessibleDict({("a", "b"): "exact", "a": {"b": "traversed"}})
        assert config.get(("a", "b")) == "exact"

    def test_empty_tuple_and_dig_false_return_default(self):
        config = _load({"a": {"b": 1}})
        assert config.get(("a", "b"), "D", dig=False) == "D"
        assert config.get((), "D") == "D"

    def test_dotted_string_does_not_match_nested_dotted_keys(self):
        # Exact-key precedence is top level only; the tuple form reaches these.
        config = _load({"metadata": {"labels": {"app.kubernetes.io/name": "web"}}})
        assert config.get("metadata.labels.app.kubernetes.io/name", "M") == "M"

    def test_reads_through_a_later_defaultdict_never_insert(self):
        config = DotAccessibleDict({})
        counters = collections.defaultdict(int)
        config["counters"] = counters
        assert config.get("counters.misses", "D") == "D"
        assert config.get(("counters", "misses"), "D") == "D"
        assert dict(counters) == {}


class TestAttributeProtocol:
    """Attribute access mirrors item access, except where the class wins."""

    def test_missing_attribute_names_the_real_class(self):
        config = _load({"a": 1})
        with pytest.raises(AttributeError, match="DotAccessibleDict"):
            config.nope

    def test_key_shadowed_by_a_method_is_reachable_by_item_access(self):
        config = _load({"items": [1, 2], "get": "g", "copy": "c"})
        assert config["items"] == [1, 2]
        assert config["get"] == "g"
        assert callable(config.items)

    @pytest.mark.parametrize("name", ["items", "get", "copy", "keys"])
    def test_writing_a_shadowed_name_is_refused(self, name):
        config = _load({"a": 1})
        with pytest.raises(AttributeError, match="read-only"):
            setattr(config, name, "x")
        with pytest.raises(AttributeError, match="read-only"):
            delattr(config, name)

    def test_subclass_can_still_set_real_instance_attributes(self):
        class Tagged(DotAccessibleDict):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                object.__setattr__(self, "tag", "t")

        tagged = Tagged({"a": 1})
        assert tagged.tag == "t"
        assert "tag" not in tagged

    def test_subclass_type_survives_conversion(self):
        class Tagged(DotAccessibleDict):
            pass

        inner = Tagged({"x": 1})
        converted = to_dot_access({"in": inner})
        assert type(converted["in"]) is Tagged
        assert converted["in"] is not inner

    def test_delattr_removes_a_key(self):
        config = _load({"a": 1, "b": 2})
        del config.a
        assert list(config) == ["b"]
        with pytest.raises(AttributeError, match="has no attribute"):
            del config.nope

    def test_copy_keeps_the_class_and_shares_values(self):
        config = _load({"db": {"host": "h"}})
        duplicate = config.copy()
        assert type(duplicate) is DotAccessibleDict
        assert duplicate["db"] is config["db"]

    def test_or_operators_keep_the_class(self):
        config = _load({"a": 1})
        assert type(config | {"b": 2}) is DotAccessibleDict
        assert (config | {"a": 9})["a"] == 9
        assert type({"b": 2} | config) is DotAccessibleDict
        assert ({"a": 9} | config)["a"] == 1
        assert config.__or__(3) is NotImplemented
        assert config.__ror__(3) is NotImplemented

    def test_or_stores_values_as_given(self):
        plain = {"x": 1}
        merged = _load({"a": 1}) | {"b": plain}
        assert merged["b"] is plain

    def test_ior_keeps_the_class(self):
        config = _load({"a": 1})
        config |= {"b": 2}
        assert type(config) is DotAccessibleDict
        assert config["b"] == 2

    def test_dict_gives_a_plain_dict_back(self):
        config = _load({"a": 1})
        assert type(dict(config)) is dict

    def test_exported_from_the_package_root(self):
        assert yaconfiglib.DotAccessibleDict is DotAccessibleDict
        assert "DotAccessibleDict" in yaconfiglib.__all__
