// Khachiwhisper.app launcher (Cocoa).
//
// Runs the Python app as a child process and stays alive as the "responsible
// process", so macOS attributes Accessibility / Microphone permissions to
// Khachiwhisper.app rather than to Python. Being a real NSApplication also
// means Launch Services talks to us: re-opening the app from Raycast, Spotlight
// or Finder sends a reopen event, which we forward to the child as SIGUSR1 so
// it can show a status notice; Quit from anywhere forwards SIGTERM.
//
// Layouts:
//   release:  Khachiwhisper.app/Contents/Resources/python/bin/python3 + Resources/khachiwhisper.py
//   dev:      <repo>/Khachiwhisper.app/Contents/MacOS/Khachiwhisper -> <repo>/.venv/bin/python + <repo>/khachiwhisper.py
#import <Cocoa/Cocoa.h>
#include <signal.h>
#include <spawn.h>
#include <sys/wait.h>

extern char **environ;
static pid_t child = 0;

@interface Launcher : NSObject <NSApplicationDelegate>
@end

@implementation Launcher
- (BOOL)applicationShouldHandleReopen:(NSApplication *)sender hasVisibleWindows:(BOOL)flag {
    if (child > 0) kill(child, SIGUSR1);
    return NO;
}
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)sender {
    if (child > 0) {
        kill(child, SIGTERM);
        for (int i = 0; i < 30 && waitpid(child, NULL, WNOHANG) == 0; i++) usleep(100000);
    }
    return NSTerminateNow;
}
@end

static void forward(int sig) { if (child > 0) kill(child, sig); }

int main(int argc, char **argv) {
    @autoreleasepool {
        NSBundle *bundle = [NSBundle mainBundle];
        NSString *contents = [[bundle bundlePath] stringByAppendingPathComponent:@"Contents"];
        NSString *py = [contents stringByAppendingPathComponent:@"Resources/python/bin/python3"];
        NSString *script = [contents stringByAppendingPathComponent:@"Resources/khachiwhisper.py"];
        NSString *workdir = [contents stringByAppendingPathComponent:@"Resources"];
        NSFileManager *fm = [NSFileManager defaultManager];
        if (![fm fileExistsAtPath:py] || ![fm fileExistsAtPath:script]) {
            NSString *repo = [[bundle bundlePath] stringByDeletingLastPathComponent];   // dev layout
            py = [repo stringByAppendingPathComponent:@".venv/bin/python"];
            script = [repo stringByAppendingPathComponent:@"khachiwhisper.py"];
            workdir = repo;
        }
        chdir([workdir fileSystemRepresentation]);
        setenv("PYTHONUNBUFFERED", "1", 1);

        char *args[argc + 3];
        args[0] = (char *)[py fileSystemRepresentation];
        args[1] = (char *)[script fileSystemRepresentation];
        int n = 2;
        for (int i = 1; i < argc; i++) {
            if (strncmp(argv[i], "-psn_", 5) == 0) continue;   // Launch Services noise
            args[n++] = argv[i];
        }
        args[n] = NULL;

        signal(SIGTERM, forward); signal(SIGINT, forward); signal(SIGHUP, forward);
        if (posix_spawn(&child, args[0], NULL, NULL, args, environ) != 0) { perror("spawn"); return 1; }

        // Exit when the child exits (with its status).
        dispatch_async(dispatch_get_global_queue(DISPATCH_QUEUE_PRIORITY_DEFAULT, 0), ^{
            int status = 0;
            while (waitpid(child, &status, 0) < 0) {}
            child = 0;
            exit(WIFEXITED(status) ? WEXITSTATUS(status) : 1);
        });

        NSApplication *app = [NSApplication sharedApplication];
        Launcher *delegate = [[Launcher alloc] init];
        [app setDelegate:delegate];
        [app setActivationPolicy:NSApplicationActivationPolicyAccessory];
        [app run];
    }
    return 0;
}
