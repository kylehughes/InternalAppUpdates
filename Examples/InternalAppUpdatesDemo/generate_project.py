#!/usr/bin/env python3
"""Generate the sample project without a third-party project generator."""

from pathlib import Path
import plistlib

root = Path(__file__).resolve().parent
project = root / "InternalAppUpdatesDemo.xcodeproj"
project.mkdir(exist_ok=True)
(project / "project.pbxproj").write_text(r'''// !$*UTF8*$!
{
    archiveVersion = 1;
    classes = {};
    objectVersion = 77;
    objects = {
        B10000000000000000000001 = {isa = PBXBuildFile; productRef = B10000000000000000000002; };
        B10000000000000000000002 = {isa = XCSwiftPackageProductDependency; productName = InternalAppUpdates; };
        B10000000000000000000003 = {isa = XCLocalSwiftPackageReference; relativePath = ../..; };
        B10000000000000000000004 = {isa = PBXFileReference; explicitFileType = wrapper.application; includeInIndex = 0; path = InternalAppUpdatesDemo.app; sourceTree = BUILT_PRODUCTS_DIR; };
        B10000000000000000000005 = {isa = PBXFileSystemSynchronizedRootGroup; exceptions = (); explicitFileTypes = {}; explicitFolders = (); path = App; sourceTree = "<group>"; };
        B10000000000000000000006 = {isa = PBXFileReference; lastKnownFileType = text.plist.xml; path = Info.plist; sourceTree = "<group>"; };
        B10000000000000000000007 = {isa = PBXGroup; children = (B10000000000000000000005, B10000000000000000000006, B10000000000000000000008); sourceTree = "<group>"; };
        B10000000000000000000008 = {isa = PBXGroup; children = (B10000000000000000000004); name = Products; sourceTree = "<group>"; };
        B10000000000000000000009 = {isa = PBXFrameworksBuildPhase; buildActionMask = 2147483647; files = (B10000000000000000000001); runOnlyForDeploymentPostprocessing = 0; };
        B1000000000000000000000A = {isa = PBXSourcesBuildPhase; buildActionMask = 2147483647; files = (); runOnlyForDeploymentPostprocessing = 0; };
        B1000000000000000000000B = {isa = PBXResourcesBuildPhase; buildActionMask = 2147483647; files = (); runOnlyForDeploymentPostprocessing = 0; };
        B1000000000000000000000C = {
            isa = PBXNativeTarget;
            buildConfigurationList = B10000000000000000000011;
            buildPhases = (B10000000000000000000009, B1000000000000000000000A, B1000000000000000000000B);
            buildRules = (); dependencies = ();
            fileSystemSynchronizedGroups = (B10000000000000000000005);
            name = InternalAppUpdatesDemo;
            packageProductDependencies = (B10000000000000000000002);
            productName = InternalAppUpdatesDemo;
            productReference = B10000000000000000000004;
            productType = "com.apple.product-type.application";
        };
        B1000000000000000000000D = {
            isa = PBXProject;
            attributes = {BuildIndependentTargetsInParallel = YES; LastUpgradeCheck = 2600; };
            buildConfigurationList = B10000000000000000000010;
            developmentRegion = en;
            hasScannedForEncodings = 0;
            knownRegions = (en, Base);
            mainGroup = B10000000000000000000007;
            minimizedProjectReferenceProxies = 1;
            packageReferences = (B10000000000000000000003);
            preferredProjectObjectVersion = 77;
            productRefGroup = B10000000000000000000008;
            projectDirPath = ""; projectRoot = "";
            targets = (B1000000000000000000000C);
        };
        B10000000000000000000010 = {isa = XCConfigurationList; buildConfigurations = (B10000000000000000000020, B10000000000000000000021); defaultConfigurationIsVisible = 0; defaultConfigurationName = Release; };
        B10000000000000000000011 = {isa = XCConfigurationList; buildConfigurations = (B10000000000000000000022, B10000000000000000000023); defaultConfigurationIsVisible = 0; defaultConfigurationName = Release; };
        B10000000000000000000020 = {isa = XCBuildConfiguration; name = Debug; buildSettings = {
            CLANG_ENABLE_MODULES = YES; SDKROOT = iphoneos; IPHONEOS_DEPLOYMENT_TARGET = 16.0;
            SWIFT_VERSION = 6.0; SWIFT_OPTIMIZATION_LEVEL = "-Onone"; SWIFT_ACTIVE_COMPILATION_CONDITIONS = "DEBUG $(inherited)";
        }; };
        B10000000000000000000021 = {isa = XCBuildConfiguration; name = Release; buildSettings = {
            CLANG_ENABLE_MODULES = YES; SDKROOT = iphoneos; IPHONEOS_DEPLOYMENT_TARGET = 16.0;
            SWIFT_VERSION = 6.0; SWIFT_OPTIMIZATION_LEVEL = "-O"; SWIFT_COMPILATION_MODE = wholemodule;
        }; };
        B10000000000000000000022 = {isa = XCBuildConfiguration; name = Debug; buildSettings = {
            INTERNAL_APP_UPDATES_FEED_URL = ""; CODE_SIGN_STYLE = Automatic;
            CURRENT_PROJECT_VERSION = 1; MARKETING_VERSION = 1.0;
            GENERATE_INFOPLIST_FILE = NO; INFOPLIST_FILE = Info.plist;
            PRODUCT_BUNDLE_IDENTIFIER = com.example.InternalAppUpdatesDemo; PRODUCT_NAME = "$(TARGET_NAME)";
            SUPPORTED_PLATFORMS = "iphoneos iphonesimulator"; SUPPORTS_MACCATALYST = NO;
            SWIFT_EMIT_LOC_STRINGS = YES; TARGETED_DEVICE_FAMILY = "1,2";
        }; };
        B10000000000000000000023 = {isa = XCBuildConfiguration; name = Release; buildSettings = {
            INTERNAL_APP_UPDATES_FEED_URL = ""; CODE_SIGN_STYLE = Automatic;
            CURRENT_PROJECT_VERSION = 1; MARKETING_VERSION = 1.0;
            GENERATE_INFOPLIST_FILE = NO; INFOPLIST_FILE = Info.plist;
            PRODUCT_BUNDLE_IDENTIFIER = com.example.InternalAppUpdatesDemo; PRODUCT_NAME = "$(TARGET_NAME)";
            SUPPORTED_PLATFORMS = "iphoneos iphonesimulator"; SUPPORTS_MACCATALYST = NO;
            SWIFT_EMIT_LOC_STRINGS = YES; TARGETED_DEVICE_FAMILY = "1,2";
        }; };
    };
    rootObject = B1000000000000000000000D;
}
''')

scheme_dir = project / "xcshareddata" / "xcschemes"
scheme_dir.mkdir(parents=True, exist_ok=True)
reference = '<BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="B1000000000000000000000C" BuildableName="InternalAppUpdatesDemo.app" BlueprintName="InternalAppUpdatesDemo" ReferencedContainer="container:InternalAppUpdatesDemo.xcodeproj"/>'
(scheme_dir / "InternalAppUpdatesDemo.xcscheme").write_text(f'''<?xml version="1.0" encoding="UTF-8"?>
<Scheme LastUpgradeVersion="2600" version="1.3">
  <BuildAction parallelizeBuildables="YES" buildImplicitDependencies="YES"><BuildActionEntries><BuildActionEntry buildForTesting="YES" buildForRunning="YES" buildForProfiling="YES" buildForArchiving="YES" buildForAnalyzing="YES">{reference}</BuildActionEntry></BuildActionEntries></BuildAction>
  <TestAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" shouldUseLaunchSchemeArgsEnv="YES"/>
  <LaunchAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" launchStyle="0" useCustomWorkingDirectory="NO" ignoresPersistentStateOnLaunch="NO" debugDocumentVersioning="YES" allowLocationSimulation="YES"><BuildableProductRunnable runnableDebuggingMode="0">{reference}</BuildableProductRunnable></LaunchAction>
  <ProfileAction buildConfiguration="Release" shouldUseLaunchSchemeArgsEnv="YES" useCustomWorkingDirectory="NO" debugDocumentVersioning="YES"><BuildableProductRunnable runnableDebuggingMode="0">{reference}</BuildableProductRunnable></ProfileAction>
  <AnalyzeAction buildConfiguration="Debug"/>
  <ArchiveAction buildConfiguration="Release" revealArchiveInOrganizer="YES"/>
</Scheme>
''')

with (root / "Info.plist").open("wb") as output:
    plistlib.dump({
        "InternalAppUpdatesFeedURL": "$(INTERNAL_APP_UPDATES_FEED_URL)",
        "CFBundleDevelopmentRegion": "$(DEVELOPMENT_LANGUAGE)",
        "CFBundleDisplayName": "Internal App Updates",
        "CFBundleExecutable": "$(EXECUTABLE_NAME)",
        "CFBundleIdentifier": "$(PRODUCT_BUNDLE_IDENTIFIER)",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": "$(PRODUCT_NAME)",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "$(MARKETING_VERSION)",
        "CFBundleVersion": "$(CURRENT_PROJECT_VERSION)",
        "LSRequiresIPhoneOS": True,
        "UIApplicationSceneManifest": {"UIApplicationSupportsMultipleScenes": False},
        "UILaunchScreen": {},
        "UISupportedInterfaceOrientations": ["UIInterfaceOrientationPortrait", "UIInterfaceOrientationLandscapeLeft", "UIInterfaceOrientationLandscapeRight"],
        "UISupportedInterfaceOrientations~ipad": ["UIInterfaceOrientationPortrait", "UIInterfaceOrientationPortraitUpsideDown", "UIInterfaceOrientationLandscapeLeft", "UIInterfaceOrientationLandscapeRight"],
    }, output)
print(project)
