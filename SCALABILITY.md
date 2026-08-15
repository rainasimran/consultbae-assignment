\# Scalability Plan



\## What breaks first



The first bottlenecks would likely be file uploads, local file storage, synchronous audio processing, and SQLite database writes.



\## Storage



For 5,000 workers, audio files should not be stored permanently on the application server. I would use object storage such as Amazon S3 or an equivalent service.



\## Upload handling



Large uploads should be handled with size limits, validation, resumable or direct-to-storage uploads where appropriate, and clear failure responses.



\## Audio processing



Audio metadata extraction should move to a background job queue rather than blocking the web request.



A worker could process:

\- duration

\- sample rate

\- bitrate

\- loudness

\- quality estimate



\## Database



SQLite is suitable for the take-home assignment, but I would move to PostgreSQL for production because multiple workers and concurrent writes would make SQLite a bottleneck.



\## Reliability



I would add:

\- retries

\- failed-job tracking

\- idempotency

\- upload status

\- monitoring

\- logging



\## Duplicate submissions



Each submission should have a unique submission ID and an idempotency mechanism to prevent accidental duplicate processing.



\## Cost



Object storage and asynchronous workers would introduce additional costs, so I would monitor storage growth, processing time, bandwidth, and database usage.



\## Production architecture



Browser

↓

Load balancer

↓

Web application

↓

Object storage

↓

Message queue

↓

Audio processing workers

↓

PostgreSQL

