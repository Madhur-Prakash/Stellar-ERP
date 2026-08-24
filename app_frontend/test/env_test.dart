import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:stellarerp_desktop/core/env.dart';

/// What `Env` does with the contents of `.env`.
///
/// Worth pinning because the values moved from compile-time `--dart-define` to a file read
/// at start-up: a missing key used to be impossible past the compiler and is now an
/// ordinary runtime case. The *uninitialised* case is covered by every other test in this
/// suite - none of them load dotenv, and they would all fail on a `NotInitializedError`.
///
/// `appName` is the subject rather than `apiBaseUrl` because the URL fields are
/// `static final`: they memoise on first read, so their value would depend on which test
/// ran first.
void main() {
  test('reads a value from the file', () {
    dotenv.testLoad(fileInput: 'APP_NAME=Acme Books');
    expect(Env.appName, 'Acme Books');
  });

  test('falls back to the default when the key is absent', () {
    dotenv.testLoad(fileInput: 'SOMETHING_ELSE=1');
    expect(Env.appName, 'Stellar ERP');
  });

  test('treats a blank value as absent', () {
    // `APP_NAME=` in a .env is a mistake, not an instruction to show an empty title.
    dotenv.testLoad(fileInput: 'APP_NAME=   ');
    expect(Env.appName, 'Stellar ERP');
  });

  test('trims surrounding whitespace', () {
    dotenv.testLoad(fileInput: 'APP_NAME=  Acme Books  ');
    expect(Env.appName, 'Acme Books');
  });
}
