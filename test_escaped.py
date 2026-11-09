"""Function boundaries in the JavaScript programs carried by reviewer probes."""
import subprocess
import unittest

import ceo


class FunctionLiterals(unittest.TestCase):
    def test_ascii_function_declarations_allow_regex_before_raw_templates(self):
        word = "\u041f"
        after = f" /'/.test('x'); const raw = String.raw`{word}`; if (raw !== '{word}') throw Error(raw);"
        for declaration in ("function f() {}", "async function f() {}", "function* f() {}",
                            "async function* f() {}", "function f(x = function () {}) {}",
                            "function f() { function inner() {} }", "const x = 1\nfunction f() {}",
                            "const x = 1\nasync function f() {}", "const x = 1\nasync function* f() {}",
                            "const x = /x/\nfunction f() {}", "const x = /x/\nasync function f() {}",
                            "class C {} function f() {}",
                            "export function f() {}", "export default function () {}",
                            "export default async function* () {}"):
            with self.subTest(declaration=declaration):
                command = f"node --input-type=module <<'JS'\n{declaration}{after}\nJS"
                self.assertEqual(ceo.escaped(command), command.replace(f"'{word}'", r"'\u041f'"))
        for wrapper in ("function outer() { %s } outer();", "if (true) { %s }",
                        "const outer = function() { %s }; outer();", "(() => { %s })();",
                        "switch (0) { case 0: %s }", "switch (0) { default: %s }",
                        "label: %s", "function outer() { return\n%s }",
                        "function* outer() { yield\n%s } const it = outer(); it.next(); it.next();"):
            with self.subTest(wrapper=wrapper):
                command = "node <<'JS'\n" + wrapper % ("function f() {}" + after) + "\nJS"
                self.assertEqual(ceo.escaped(command), command.replace(f"'{word}'", r"'\u041f'"))

    def test_ascii_function_expressions_still_allow_division(self):
        word = "\u041f"
        for function in ("function() {}", "function f() {}", "async function() {}",
                         "function*() {}", "async function* f() {}", "function(a = function() {}) {}"):
            for wrapper in ("const n = %s;", "const n = `${%s}`;", "function f() { return %s; }",
                            "const obj = { f: %s };", "const n = true ? 1 : %s;"):
                with self.subTest(function=function, wrapper=wrapper):
                    program = wrapper % (function + f" / '{word}'.length / 2")
                    command = f"node <<'JS'\n{program}\nJS"
                    self.assertEqual(ceo.escaped(command), command.replace(word, r"\u041f"))

    def test_ascii_async_modifier_cannot_cross_a_line_break(self):
        word = "\u041f"
        for separator in ("\n", "/*\n*/", "\r", "\u2028", "\u2029"):
            declarations = (f"const async = 1; async{separator}function f() {{ return await`{word}`; }}",
                            f"class C {{ async{separator}*f() {{ yield await`{word}`; }} }}",
                            f"class C {{ async{separator}f() {{ return await`{word}`; }} }}",
                            f"class C {{ async{separator}['f']() {{ return await`{word}`; }} }}")
            calls = ("f()", "new C().f().next().value", "new C().f()", "new C().f()")
            for declaration, call in zip(declarations, calls):
                with self.subTest(separator=separator, declaration=declaration):
                    command = (f"node <<'JS'\nconst await = String.raw; {declaration}\n"
                               f"const s = {call}; if (s !== '{word}') throw Error(s);\nJS")
                    self.assertEqual(ceo.escaped(command), command.replace(f"'{word}'", r"'\u041f'"))

    def test_ascii_private_async_generator_methods(self):
        word = "\u041f"
        command = (f"node <<'JS'\nclass C {{ async *#f() {{ yield await `{word}`; }} f() {{ return this.#f(); }} }}\n"
                   f"new C().f().next().then(({{value}}) => {{ if (value !== '{word}') throw Error(value); }});\nJS")
        self.assertEqual(ceo.escaped(command), command.replace(word, r"\u041f"))

    def test_ascii_regex_literals_are_expression_operands(self):
        word = "\u041f"
        command = f"node <<'JS'\nconst n = /x/ / '{word}'.length / 2; const raw = String.raw`{word}`;\nJS"
        self.assertEqual(ceo.escaped(command), command.replace(f"'{word}'", r"'\u041f'"))

    def test_ascii_async_generator_methods_keep_await_and_yield_operators(self):
        word = "\u041f"
        for method in ("async *f", "async /* comment */ * /* comment */ f", "async *'f'",
                       'async *"f"', "async *['f']", "async *[(() => 'f')()]", "async ['f']"):
            for wrapper in ("const obj = { %s };", "class Obj { %s }", "class Obj { static %s }"):
                with self.subTest(method=method, wrapper=wrapper):
                    body = f"const s = await `{word}`;"
                    if "*" in method:
                        body += f" yield `{word}`;"
                    command = "node <<'JS'\n" + wrapper % (method + "() { " + body + " }") + "\nJS"
                    self.assertEqual(ceo.escaped(command), command.replace(word, r"\u041f"))

    def test_ascii_method_context_does_not_reach_computed_names_or_other_functions(self):
        word = "\u041f"
        for name in ("await", "yield"):
            raw = f"{name}`{word}`"
            command = (f"node <<'JS'\nconst {name} = String.raw; const async = 2;\n"
                       f"const obj = {{ value: async * String({raw}), async *[{raw}]() {{\n"
                       f"const s = await `{word}`; yield `{word}`;\n"
                       f"function inner(s = {raw}) {{ return {raw}; }}\n"
                       f"const other = {{ f() {{ return {raw}; }} }};\n"
                       f"}}, f() {{ return {raw}; }} }}; const last = {raw};\nJS")
            expected = command.replace(f"await `{word}`", r"await `\u041f`").replace(
                f"yield `{word}`", r"yield `\u041f`")
            with self.subTest(name=name):
                self.assertEqual(ceo.escaped(command), expected)


class InlineLiterals(unittest.TestCase):
    def test_ascii_inline_programs_escape_only_their_ordinary_literals(self):
        word = "\u041f"
        node = (f'`{word}` + String.raw`{word}` + /{word}/.source + "{word}" // {word}',
                r'`\u041f` + String.raw`%s` + /%s/.source + "\u041f" // %s' % (word, word, word))
        python = (f'(r"{word}", b"x", "{word}")  # {word}', r'(r"%s", b"x", "\u041f")  # %s' % (word, word))
        for opener in ("node -e", "node --eval", "node -p", "node --print", "LC_ALL=C node --input-type=module -e",
                       "python -c", "python3 -I -B -c", "python3.12 -c"):
            for quote in ("'", '"'):
                # Bash runs a backtick in double quotes as a command: only a plain program is quoted that way.
                pair = (node if quote == "'" else [text.split(" + ", 2)[2] for text in node]) if "node" in opener else python
                program, expected = pair if quote == "'" else (text.replace('"', '\\"') for text in pair)
                command, expected = (f"{opener} {quote}{text}{quote} '{word}' \"{word}\"" for text in (program, expected))
                with self.subTest(opener=opener, quote=quote):
                    self.assertEqual(ceo.escaped(command), expected)
                    self.assertEqual(ceo.escaped(expected), expected)

    def test_ascii_double_quoted_programs_keep_what_bash_passes(self):
        word = "\u041f"
        # Bash turns \\ into \ and \" into ": the program sees '\\' + word, and its backslashes get back the escapes Bash needs.
        for command, expected in ((f'python3 -c "print(\\"{word}\\", \'\\\\\\\\{word}\')"',
                                   'python3 -c "print(\\"\\u041f\\", \'\\\\\\\\\\u041f\')"'),
                                  (f'python3 -c "print(\'\\q{word}\\\\\\\\\')"', 'python3 -c "print(\'\\q\\u041f\\\\\\\')"'),
                                  (f'python3 -c "print(\'\\\\{word}\')"', f'python3 -c "print(\'\\\\{word}\')"')):
            with self.subTest(command=command):
                self.assertEqual(ceo.escaped(command), expected)
                ran = [subprocess.run(["bash", "--noprofile", "--norc", "-c", text], capture_output=True, timeout=10)
                       for text in (command, expected)]
                self.assertEqual([one.returncode for one in ran], [0, 0])
                self.assertEqual(ran[0].stdout, ran[1].stdout)

    def test_ascii_words_that_are_not_an_inline_program_keep_their_bytes(self):
        word = "\u041f"
        for command in (f'node -e "console.log(\'$HOME{word}\')"', f'node -e "`{word}`"', f"node -e $'\"{word}\"'",
                        f"node -e '\"{word}\"'\"x\"", f"node --eval='\"{word}\"'", f"python3 - -c '\"{word}\"'",
                        f"node -- -e '\"{word}\"'", f"python3 -Ec -c '\"{word}\"'", f"python3 -m x -c '\"{word}\"'",
                        f"node -r mod -e '\"{word}\"'", f"node -i -e -e '\"{word}\"'", f"python3 -c 'print(1)' '\"{word}\"'",
                        f"echo node -e '\"{word}\"'", f"cd x && node -e '\"{word}\"'", f"# node -e '\"{word}\"'",
                        f"cat <<'D'\nnode -e '\"{word}\"'\nD", f"python3 -c '\"{word}' x '\"",
                        f"node -c '\"{word}\"'", f"python3 -e '\"{word}\"'"):
            with self.subTest(command=command):
                self.assertEqual(ceo.escaped(command), command)

    def test_ascii_multiline_shell_data_keeps_its_bytes(self):
        word = "\u041f"
        probe, js = f"node -e '\"{word}\"'", f"node <<JS\nconsole.log(\"{word}\")\nJS"
        # A quote, array or ${ left open, or a trailing backslash, carries a command-looking line as data.
        for script in (f"printf '%s\\n' '\nnode -e \"console.log(\\\"{word}\\\")\"\n'", f"printf '%s\\n' '\n{js}\n'",
                       f"printf '%s\\n' \"\n{js.replace(chr(34), chr(39))}\n\"", f"printf '%s\\n' $'\n{js}\n'",
                       f"items=(\n{probe}\n)\nprintf '%s\\n' \"${{items[@]}}\"", f"items=( {probe} )\nprintf '%s\\n' \"${{items[@]}}\"",
                       f"printf '%s\\n' \\\n{probe}", f"x=; printf '%s\\n' ${{x:-\n{probe}\n}}"):
            with self.subTest(script=script):
                ran = [subprocess.run(["bash", "--noprofile", "--norc", "-c", text], capture_output=True, timeout=10)
                       for text in (script, ceo.escaped(script))]
                self.assertEqual([one.returncode for one in ran], [0, 0])
                self.assertIn(word.encode(), ran[0].stdout)
                self.assertEqual(ran[1].stdout, ran[0].stdout)
                self.assertEqual(ceo.escaped(script), script)
        # Lines before an open quote, and after an array closed on its line, still start commands.
        for script in (f"{probe}\nprintf '%s\\n' '\n{probe}\n'", f"items=(a b)\n{probe}"):
            with self.subTest(script=script):
                self.assertEqual(ceo.escaped(script), script.replace(word, r"\u041f", 1))

    def test_ascii_brace_in_quoted_or_commented_text_lets_later_programs_escape(self):
        word = "\u041f"
        # Only an unquoted ${ outside a comment opens an expansion that runs on into the next line.
        for first in ("printf '%s\\n' '${'", "# literal ${ in a comment", "echo ok # ${", "printf '%s\\n' a'${'b",
                      "printf '%s\\n' \\${", "printf '%s\\n' $'${'", "printf '%s\\n' \"\\${\""):
            for program in (f"node <<'JS'\nconsole.log('{word}')\nJS", f"node -e '\"{word}\"'"):
                command = f"{first}\n{program}"
                with self.subTest(command=command):
                    self.assertEqual(ceo.escaped(command), command.replace(word, r"\u041f"))
