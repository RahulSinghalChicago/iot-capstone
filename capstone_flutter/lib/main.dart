import 'dart:async';
import 'dart:io';
import 'package:fluttertoast/fluttertoast.dart';

import 'package:amplify_api/amplify_api.dart';
import 'package:amplify_authenticator/amplify_authenticator.dart';
import 'package:amplify_push_notifications_pinpoint/amplify_push_notifications_pinpoint.dart';
import 'package:amplify_storage_s3/amplify_storage_s3.dart';

import 'package:capstone_flutter/managecapimage.dart';
import 'package:flutter/material.dart';
import 'package:flutter_aws_s3_client/flutter_aws_s3_client.dart';

import 'package:go_router/go_router.dart';

// Amplify Flutter Packages
import 'package:amplify_flutter/amplify_flutter.dart';
import 'package:amplify_auth_cognito/amplify_auth_cognito.dart';

// Generated in previous step
import 'amplifyconfiguration.dart';
import 'models/ModelProvider.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();

  _configureAmplify();
  runApp(const MyApp());
}

/**
 * This was the attempt to make real mobile notifications work. 
 */
void handlePermissions(AmplifyPushNotificationsPinpoint pushPlugin) async {
  final status = await pushPlugin.getPermissionStatus();
  if (status == PushNotificationPermissionStatus.granted) {
    // no further action is required, user has already granted permissions
    return;
  }
  if (status == PushNotificationPermissionStatus.denied) {
    // further attempts to request permissions will no longer do anything
    return;
  }
  if (status == PushNotificationPermissionStatus.shouldRequest) {
    // go ahead and request permissions from the user
    await pushPlugin.requestPermissions();
  }
  if (status == PushNotificationPermissionStatus.shouldExplainThenRequest) {
    // you should display some explanation to your user before requesting permissions
    //await myFunctionExplainingPermissionsRequest();
    // then request permissions
    await pushPlugin.requestPermissions();
  }
}

// Note: This handler does not *need* to be async, but it can be!
Future<void> myAsyncNotificationReceivedHandler(
    PushNotificationMessage notification) async {
  // Process the received push notification message in the background
  safePrint('Received notification');
}

Future<void> _configureAmplify() async {
  // Add any Amplify plugins you want to use

  try {
    if (!Amplify.isConfigured) {
      // Create the API plugin.
      //
      // If `ModelProvider.instance` is not available, try running
      // `amplify codegen models` from the root of your project.
      final api = AmplifyAPI(modelProvider: ModelProvider.instance);
      //final pushPlugin = AmplifyPushNotificationsPinpoint();

      // Should be added in the main function to avoid missing events.
      //pushPlugin.onNotificationReceivedInBackground(
      //   myAsyncNotificationReceivedHandler);
      final s3 = AmplifyStorageS3();

      // Create the Auth plugin.
      final auth = AmplifyAuthCognito();

      // Add the plugins and configure Amplify for your app.
      //await Amplify.addPlugins([api, auth, pushPlugin]);
      await Amplify.addPlugins([api, auth, s3]);
      await Amplify.configure(amplifyconfig);

      //handlePermissions(pushPlugin);

      // await Firebase.initializeApp(
      //   options: DefaultFirebaseOptions.currentPlatform,
      // );

      safePrint('Successfully configured');

      Amplify.Hub.listen(HubChannel.Auth, (event) {
        switch (event.eventName) {
          case "SIGNED_IN":
            safePrint("User is signed in");
            break;
          case "SIGNED_OUT":
            safePrint("User is signed out");
            break;
          case "SESSION_EXPIRED":
            safePrint("User session is expired");
            break;
        }
      });
    }
  } on Exception catch (e) {
    safePrint('Error configuring Amplify: $e');
  }
}

class MyApp extends StatelessWidget {
  const MyApp({super.key});

  // GoRouter configuration
  static final _router = GoRouter(
    routes: [
      GoRoute(
        path: '/',
        builder: (context, state) => const HomeScreen(
          title: 'Main',
        ),
      ),
    ],
  );

  // This widget is the root of your application.
  @override
  Widget build(BuildContext context) {
    return Authenticator(
        child: MaterialApp.router(
      routerConfig: _router,
      builder: Authenticator.builder(),
    ));
  }
}

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.title});

  // This widget is the home page of your application. It is stateful, meaning
  // that it has a State object (defined below) that contains fields that affect
  // how it looks.

  // This class is the configuration for the state. It holds the values (in this
  // case the title) provided by the parent (in this case the App widget) and
  // used by the build method of the State. Fields in a Widget subclass are
  // always marked "final".

  final String title;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  var _capImages = <CapImage>[];
  CapImage? _capImage;
  var _timer;
  Image? _image;
  var _imagePath = "${Directory.systemTemp.path}/example.jpg";

  bool get _isCreate => _capImage == null;

  final _formKey = GlobalKey<FormState>();
  final TextEditingController _titleController = TextEditingController();

  @override
  initState() {
    super.initState();
    var subAdd = subscribeAdd();
    subAdd.listen((event) {
      if (event.data != null) {
        var cap = event.data ?? CapImage(name: 'blank');
        setState(() {
          _capImages.add(cap);
        });
      }
    });

    var subDelete = subscribeDelete();
    subDelete.listen((event) {
      if (event.data != null) {
        var cap = event.data ?? CapImage(name: 'blank');
        setState(() {
          _capImages.remove(cap);
        });
      }
    });

    // uploadGuestFile(
    //     filePath: "${Directory.systemTemp.path}/example.txt",
    //     key: "example.txt");

    _timer = Timer.periodic(
        const Duration(seconds: 3), (Timer t) => _refreshCapImage());
  }

  Future<void> uploadGuestFile({
    required String filePath,
    required String key,
  }) async {
    final awsFile = AWSFile.fromPath(filePath);
    const options = StorageUploadFileOptions(
      accessLevel: StorageAccessLevel.guest,
    );

    try {
      final uploadResult = await Amplify.Storage.uploadFile(
        localFile: awsFile,
        key: key,
        options: options,
      ).result;
      safePrint('Uploaded file: ${uploadResult.uploadedItem.key}');
    } on StorageException catch (e) {
      safePrint('Something went wrong uploading file: ${e.message}');
      //rethrow;
    }
  }

  /// This method downloads the image from S3 and displays on mobile screen
  Future<void> downloadImageAndDisplay(String key) async {
    try {
      const region = "us-east-1";
      const bucketId = "capstone-sp23184301-dev";
      final AwsS3Client s3client = AwsS3Client(
          region: region,
          host: "s3.$region.amazonaws.com",
          bucketId: bucketId,
          accessKey: "",
          secretKey: "");

      final response = await s3client.getObject(key);
      setState(() {
        _image = Image.memory(
          response.bodyBytes,
          fit: BoxFit.cover,
        );
      });
      safePrint("This is the output length: ${response.bodyBytes.length}");
    } on StorageException catch (e) {
      safePrint(e.message);
    }
  }

  /// This method refreshes images
  Future<void> _refreshCapImage() async {
    // To be filled in
    try {
      final request = ModelQueries.list(CapImage.classType);
      final response = await Amplify.API.query(request: request).response;

      final capImages = response.data?.items;
      if (response.hasErrors) {
        safePrint('errors: ${response.errors}');
        return;
      }

      var newCapImages = capImages!.whereType<CapImage>().toList();
      var foundNewEntry = false;

      for (var newImg in newCapImages) {
        if (!_capImages.contains(newImg)) {
          _capImage = newImg;
          foundNewEntry = true;
          downloadImageAndDisplay(_capImage!.path!);
        }
      }

      if (!foundNewEntry) {
        return;
      }

      if (_capImage != null && _capImage!.name != "UNKNOWN") {
        _titleController.text = "${_capImage!.name} is at door!";
      } else {
        _titleController.text = "";
      }

      setState(() {
        _capImages = newCapImages;
        _capImage;
        _titleController;
      });
    } on ApiException catch (e) {
      safePrint('Query failed: $e');
    }
  }

  /// Shows toast on the screen when new user is saved
  Future<bool?> toast(String message) {
    Fluttertoast.cancel();
    return Fluttertoast.showToast(
        msg: message,
        toastLength: Toast.LENGTH_LONG,
        gravity: ToastGravity.BOTTOM,
        timeInSecForIosWeb: 4,
        backgroundColor: Colors.green[200],
        textColor: Colors.white,
        fontSize: 15.0);
  }

  /// Submit the identified image to server
  Future<void> submitForm() async {
    if (!_formKey.currentState!.validate()) {
      return;
    }

    // If the form is valid, submit the data
    final name = _titleController.text;

    if (_isCreate) {
      // Create a new budget entry
      final newEntry = CapImage(
        name: name,
        description: _capImage!.description,
        path: _capImage!.path,
      );

      final request = ModelMutations.create(newEntry);
      final response = await Amplify.API.mutate(request: request).response;
      safePrint('Create result: $response');
    } else {
      // Update budgetEntry instead
      final updateCapImage = _capImage!.copyWith(
        name: name,
        description: _capImage!.description,
        path: _capImage!.path,
      );
      final request = ModelMutations.update(updateCapImage);
      final response = await Amplify.API.mutate(request: request).response;
      setState(() {
        _capImage = response.data;
      });
      safePrint('Update result: $response');
      toast("Your input is saved.");
    }
  }

  // Utility methods to support delete
  Future<void> _deleteCapImage(CapImage capImage) async {
    // To be filled in
    final request = ModelMutations.delete<CapImage>(capImage);
    final response = await Amplify.API.mutate(request: request).response;
    safePrint('Delete response: $response');
    await _refreshCapImage();
  }

  /// GraphQL subscription
  Stream<GraphQLResponse<CapImage>> subscribeAdd() {
    final subscriptionRequest = ModelSubscriptions.onCreate(CapImage.classType);
    final Stream<GraphQLResponse<CapImage>> operation = Amplify.API
        .subscribe(
          subscriptionRequest,
          onEstablished: () => safePrint('Subscription established'),
        )
        // Listens to only 5 elements
        .take(5)
        .handleError(
      (Object error) {
        safePrint('Error in subscription stream: $error');
      },
    );
    return operation;
  }

  Stream<GraphQLResponse<CapImage>> subscribeDelete() {
    final subscriptionRequest = ModelSubscriptions.onDelete(CapImage.classType);
    final Stream<GraphQLResponse<CapImage>> operation = Amplify.API
        .subscribe(
          subscriptionRequest,
          onEstablished: () => safePrint('Subscription delete established'),
        )
        // Listens to only 5 elements
        .take(5)
        .handleError(
      (Object error) {
        safePrint('Error in delete subscription stream: $error');
      },
    );
    return operation;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Home Security'),
      ),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 800),
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: SingleChildScrollView(
              child: Form(
                key: _formKey,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    _image ?? const Text('Welcome to home security system!'),
                    _image != null
                        ? TextFormField(
                            controller: _titleController,
                            enabled: _titleController.text.isEmpty,
                            decoration: const InputDecoration(
                              labelText: 'Name',
                            ),
                            validator: (value) {
                              if (value == null || value.isEmpty) {
                                return 'Please enter a name';
                              }
                              return null;
                            },
                          )
                        : const Text(""),
                    _image != null && _capImage!.name == "UNKNOWN"
                        ? const Text("Do you know him?")
                        : const Text(""),
                    const SizedBox(height: 20),
                    _image != null && _capImage!.name == "UNKNOWN"
                        ? ElevatedButton(
                            onPressed: submitForm,
                            child: const Text("Identify"),
                          )
                        : const Text(""),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
